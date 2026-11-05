'use strict';
// Pokebot battle worker: wraps @pkmn/sim. Protocol = one JSON object per line on stdin -> one JSON line on stdout.
const readline = require('readline');
const { Battle, Teams, Dex, toID } = require('@pkmn/sim');

const battles = new Map();           // bid -> {b, sent, ai:{p1?,p2?}}
const SIDES = ['p1', 'p2'];

// ---- species lookup by national dex number (base forms only)
let NUM2NAME = null;
function speciesName(num) {
  if (!NUM2NAME) {
    NUM2NAME = {};
    for (const s of Dex.species.all()) if (s.num > 0 && !s.forme && !NUM2NAME[s.num]) NUM2NAME[s.num] = s.name;
  }
  return NUM2NAME[num];
}

// ---- gimmick items
function megaStoneFor(species) {
  const base = Dex.species.get(species).baseSpecies;
  for (const it of Dex.items.all()) if (it.megaStone && typeof it.megaStone === 'object' && it.megaStone[base]) return it.name;
}
function zCrystalFor(type) {
  for (const it of Dex.items.all()) if (it.zMove === true && it.zMoveType === type) return it.name;
}
function itemFor(mon, gimmick) {
  if (gimmick === 'mega') return mon.megaAuto === false ? '' : (megaStoneFor(mon.name) || '');
  if (gimmick === 'z') {
    if (mon.megaAuto === false) return '';
    let best = null;
    for (const m of mon.moves) { const mv = Dex.moves.get(m); if (mv.category !== 'Status' && (!best || mv.basePower > best.basePower)) best = mv; }
    return best ? (zCrystalFor(best.type) || '') : '';
  }
  return '';
}

function packTeam(team, gimmick) {
  const sets = team.map(m => {
    const name = m.species || speciesName(m.num);
    if (!name) throw new Error('unknown species ' + (m.species || m.num));
    const ivs = m.ivs || {}, evs = m.evs || {};
    return {
      name: m.nick || '', species: name, item: m.item || itemFor({ name, moves: m.moves, megaAuto: m.megaAuto }, gimmick), ability: m.ability || '',
      moves: m.moves, level: m.level || 50, nature: m.nature || 'Hardy', gender: m.gender || '', shiny: !!m.shiny,
      teraType: m.teraType || undefined,
      evs: { hp: evs.hp || 0, atk: evs.atk || 0, def: evs.def || 0, spa: evs.spa || 0, spd: evs.spd || 0, spe: evs.spe || 0 },
      ivs: { hp: ivs.hp ?? 31, atk: ivs.atk ?? 31, def: ivs.def ?? 31, spa: ivs.spa ?? 31, spd: ivs.spd ?? 31, spe: ivs.spe ?? 31 },
    };
  });
  return Teams.pack(sets);
}

// ---- state snapshot for the UI
const clone = o => JSON.parse(JSON.stringify(o));
function monView(p, side) {
  return {
    name: p.name, species: p.species.name, num: p.species.num, level: p.level, hp: p.hp, maxhp: p.maxhp,
    status: p.status || '', fainted: p.fainted || p.hp <= 0, active: p.isActive, types: p.getTypes ? p.getTypes() : p.types,
    shiny: !!p.set.shiny, boosts: p.isActive ? { ...p.boosts } : {},
    dynamaxed: !!p.volatiles['dynamax'], tera: p.terastallized || '', mega: /-mega/i.test(p.species.id) || /mega/i.test(p.species.forme || ''),
    ability: p.ability, moves: p.moveSlots.map(m => ({ id: m.id, name: m.move, pp: m.pp, maxpp: m.maxpp, disabled: !!m.disabled })),
    bhp: p.volatiles['dynamax'] ? Math.max(1, Math.ceil(p.hp * p.baseMaxhp / p.maxhp)) : p.hp, bmax: p.baseMaxhp || p.maxhp,
    pos: side.pokemon.indexOf(p), uid: p.set.uid ?? null,
  };
}
function snapshot(b) {
  return {
    turn: b.turn, weather: b.field.weather || '', terrain: b.field.terrain || '',
    sides: b.sides.map(s => ({ name: s.name, pokemon: s.pokemon.map(p => monView(p, s)), active: s.active.map(p => (p ? s.pokemon.indexOf(p) : -1)) })),
  };
}
function requestOf(side) {
  const r = side.activeRequest;
  return r ? clone(r) : null;
}

function reply(bid, extra) {
  const e = battles.get(bid), b = e.b;
  const log = b.log.slice(e.sent); e.sent = b.log.length;
  const wait = SIDES.filter((id, i) => { const r = b.sides[i].activeRequest; return r && !r.wait && !b.sides[i].isChoiceDone(); });
  return { ok: true, log, ended: !!b.ended, winner: b.ended ? (b.winner ? SIDES[b.sides.findIndex(s => s.name === b.winner)] : 'tie') : null,
    requests: SIDES.map((id, i) => requestOf(b.sides[i])), waiting: wait, state: snapshot(b), ...extra };
}

// ---- AI --------------------------------------------------------------------------------------------------------
function effectiveness(b, moveType, target) {
  let t = target.getTypes ? target.getTypes() : target.types;
  if (!b.dex.getImmunity(moveType, t)) return 0;
  return Math.pow(2, b.dex.getEffectiveness(moveType, t));
}
function movePower(b, user, mv, foe) {
  if (mv.category === 'Status') return 0;
  let bp = mv.basePower || (mv.damageCallback || mv.ohko ? 60 : 40);
  if (mv.multihit) bp *= Array.isArray(mv.multihit) ? 3 : mv.multihit;
  const phys = mv.category === 'Physical';
  const a = phys ? user.storedStats.atk : user.storedStats.spa, d = foe ? (phys ? foe.storedStats.def : foe.storedStats.spd) : 100;
  const stab = user.getTypes().includes(mv.type) ? 1.5 : 1;
  const eff = foe ? effectiveness(b, mv.type, foe) : 1;
  const acc = mv.accuracy === true ? 1 : mv.accuracy / 100;
  return bp * stab * eff * (a / Math.max(1, d)) * acc;
}
function aiChoose(e, sideId, opts) {
  const b = e.b, si = SIDES.indexOf(sideId), side = b.sides[si], foeSide = b.sides[1 - si], req = side.activeRequest;
  if (!req || req.wait) return '';
  const rng = e.rng;
  const alive = side.pokemon.filter(p => !p.fainted && p.hp > 0);
  const foes = foeSide.active.filter(p => p && p.hp > 0);
  const picks = [];
  const used = new Set();
  if (req.forceSwitch) {
    return req.forceSwitch.map((must, slot) => {
      if (!must) return 'pass';
      let best = -1, bi = -1;
      side.pokemon.forEach((p, i) => {
        if (p.isActive || p.fainted || p.hp <= 0 || used.has(i)) return;
        let sc = p.hp / p.maxhp * 50;
        for (const m of p.moveSlots) { const mv = Dex.moves.get(m.id); for (const f of foes) sc += movePower(b, p, mv, f) / 4; }
        if (sc > best) { best = sc; bi = i; }
      });
      if (bi < 0) return 'pass';
      used.add(bi); return 'switch ' + (bi + 1);
    }).join(', ');
  }
  const gimmick = opts && opts.gimmick;
  let gimmickUsed = false;                               // doubles: only one slot may use the gimmick per turn
  const aceOnly = !!(opts && opts.aceOnly);              // only mons flagged `ace` may use the gimmick
  req.active.forEach((a, slot) => {
    const mon = side.active[slot];
    if (!mon || mon.hp <= 0) { picks.push('pass'); return; }
    let best = -1e9, bi = 0, bt = 0;
    a.moves.forEach((m, i) => {
      if (m.disabled || (m.pp === 0 && !m.maxpp === undefined)) return;
      const mv = Dex.moves.get(m.id);
      const tg = foes.length ? foes : [null];
      let sc = 0, tgt = 1;
      tg.forEach((f, fi) => {
        let s = movePower(b, mon, mv, f);
        if (f && s > 0 && s * 0.9 >= f.hp * 1.0) s *= 1.6;           // likely KO
        if (s > sc) { sc = s; tgt = (foeSide.active.indexOf(f)) + 1; }
      });
      if (mv.category === 'Status') {
        sc = 12 + rng() * 25;
        if (mv.status && foes[0] && foes[0].status) sc = 1;
        if (mv.boosts && mv.target === 'self' && mon.boosts.atk + mon.boosts.spa >= 2) sc = 2;
        if (mv.heal && mon.hp / mon.maxhp > 0.6) sc = 1;
        if (mv.heal && mon.hp / mon.maxhp < 0.4) sc += 60;
        if (mv.id === 'protect' || mv.id === 'detect') sc = rng() < 0.15 ? 40 : 1;
      }
      sc *= 0.9 + rng() * 0.2;
      if (sc > best) { best = sc; bi = i; bt = tgt; }
    });
    const mv = a.moves[bi];
    let c = 'move ' + (bi + 1);
    if (['normal', 'any', 'adjacentFoe', 'adjacentAlly', 'adjacentAllyOrSelf'].includes(mv.target) && foeSide.active.length > 1) c += ' ' + (bt > 0 ? bt : 1);
    if (gimmick && !gimmickUsed && (!aceOnly || mon.set.ace)) {
      if (gimmick === 'mega' && a.canMegaEvo) { c += ' mega'; gimmickUsed = true; }
      else if (gimmick === 'dynamax' && a.canDynamax) {
        if (mv.target !== 'self' && foeSide.active.length > 1 && !/^move \d+ -?\d+$/.test(c)) c += ' ' + (bt > 0 ? bt : 1);   // Max Moves need a target
        c += ' dynamax'; gimmickUsed = true;
      }
      else if (gimmick === 'tera' && a.canTerastallize) { c += ' terastallize'; gimmickUsed = true; }
      else if (gimmick === 'z' && a.canZMove) {
        const zi = a.canZMove.findIndex(z => z);
        if (zi >= 0) { c = 'move ' + (zi + 1) + (foeSide.active.length > 1 ? ' 1' : '') + ' zmove'; gimmickUsed = true; }
      }
    }
    picks.push(c);
  });
  return picks.join(', ');
}

// ---- ops -------------------------------------------------------------------------------------------------------
function mulberry(seed) { let a = seed >>> 0; return () => { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }

function newBattle(req) {
  const doubles = req.format === 'doubles';
  const fmt = (doubles ? 'gen9doublescustomgame' : 'gen9customgame') + '@@@!Team Preview';
  const seed = req.seed != null ? [req.seed & 0xffff, (req.seed >> 16) & 0xffff, 1, 2] : undefined;
  const b = new Battle({ formatid: fmt, seed, p1: { name: req.p1.name || 'P1', team: packTeam(req.p1.team, req.p1.gimmick) },
    p2: { name: req.p2.name || 'P2', team: packTeam(req.p2.team, req.p2.gimmick) } });
  // Allow Dynamax in gen 9 (the gen gate normally forbids it); Tera stays available.
  for (const s of b.sides) { s.dynamaxUsed = false; s.canDynamaxNow = function () { return !this.dynamaxUsed; }; }
  const origTera = b.actions.canTerastallize.bind(b.actions);
  b.actions.canTerastallize = function (pk) { return pk.side.gimmick === 'tera' ? origTera(pk) : null; };
  // gimmick lock-in: strip the other one so it is never offered
  [req.p1, req.p2].forEach((pl, i) => {
    const g = pl.gimmick || null, s = b.sides[i];
    if (g !== 'dynamax') { s.dynamaxUsed = true; s.canDynamaxNow = function () { return false; }; }
    s.gimmick = g;
    // carry caller metadata + HP
    s.pokemon.forEach((p, k) => {
      const src = pl.team[k]; if (!src) return;
      p.set.uid = src.uid ?? null; p.set.ace = !!src.ace;
      if (src.hp != null && src.hp < p.maxhp) {
        p.hp = Math.max(0, Math.min(src.hp, p.maxhp));
        if (p.hp === 0) { p.fainted = true; p.faintQueued = false; s.pokemonLeft--; }
      }
    });
  });
  b.sides.forEach(sd => sd.pokemon.forEach(p => { p.canTerastallize = b.actions.canTerastallize(p); }));
  b.makeRequest(b.requestState || 'move');     // re-issue so the patched gimmick gates apply to turn 1
  return b;
}

const OPS = {
  new(req) {
    const b = newBattle(req);
    battles.set(req.bid, { b, sent: 0, rng: mulberry(req.seed != null ? req.seed : Date.now()) });
    // sides whose lead is fainted (HP carried over) need a switch; handled by Showdown at start if party order is right
    return reply(req.bid, {});
  },
  choose(req) {
    const e = battles.get(req.bid); if (!e) return { ok: false, error: 'no such battle' };
    const side = e.b.sides[SIDES.indexOf(req.side)];
    const before = e.b.log.length;
    const okc = e.b.choose(req.side, req.choice);
    const errs = e.b.log.slice(before).filter(l => l.startsWith('|error|'));
    if (!okc || errs.length) { return { ...reply(req.bid, {}), ok: false, error: errs.join(';') || 'invalid choice' }; }
    return reply(req.bid, {});
  },
  ai(req) {
    const e = battles.get(req.bid); if (!e) return { ok: false, error: 'no such battle' };
    const c = aiChoose(e, req.side, req);
    return { ok: true, choice: c };
  },
  // ball throw: the player's turn is spent (Recharge), the foe acts
  idle(req) {
    const e = battles.get(req.bid); if (!e) return { ok: false, error: 'no such battle' };
    const si = SIDES.indexOf(req.side), mon = e.b.sides[si].active[0];
    mon.addVolatile('mustrecharge');
    e.b.makeRequest('move');
    return OPS.choose({ bid: req.bid, side: req.side, choice: 'move 1' });
  },
  end(req) { battles.delete(req.bid); return { ok: true }; },
  species(req) { return { ok: true, name: speciesName(req.num) }; },
  ping() { return { ok: true }; },
};

const rl = readline.createInterface({ input: process.stdin });
rl.on('line', line => {
  let req, out;
  try { req = JSON.parse(line); out = OPS[req.op](req); }
  catch (err) { out = { ok: false, error: String(err && err.message || err) }; }
  out.rid = req && req.rid;
  process.stdout.write(JSON.stringify(out) + '\n');
});
