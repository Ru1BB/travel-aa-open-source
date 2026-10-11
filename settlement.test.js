import test from 'node:test';
import assert from 'node:assert/strict';
import { parseAmount, money, calculate } from './settlement.js';
const members = ['甲', '乙', '丙', '丁', '戊'].map((name, i) => ({ id: String(i), name }));
const bill = (cents, payer, participants) => ({ cents, payer, participants });
test('金额以分为单位，拒绝超额、小数超精度和非正数', () => {
  assert.equal(parseAmount('12.30'), 1230); assert.equal(parseAmount('0.01'), 1); assert.equal(parseAmount('100000000'), 10000000000);
  for (const invalid of ['0', '-1', '1.001', '1e2', 'NaN', 'Infinity', '100000000.01', '']) assert.throws(() => parseAmount(invalid));
  assert.equal(money(1001), '10.01'); assert.equal(money(-1), '-0.01');
});
test('局部分摊，垫付者可不承担，并汇总多人支出', () => {
  const r = calculate(members, [bill(10000, '0', ['0', '1', '2', '3', '4']), bill(6000, '1', ['1', '2']), bill(3000, '2', ['3', '4'])]);
  assert.equal(r.total, 19000);
  assert.deepEqual(r.rows.map(r => r.share), [2000, 5000, 5000, 3500, 3500]);
  assert.deepEqual(r.rows.map(r => r.balance), [8000, 1000, -2000, -3500, -3500]);
  assert.equal(r.transfers.length, 4);
});
test('尾差按成员添加顺序分配，勾选顺序不改变结果', () => {
  const r = calculate(members, [bill(100, '4', ['2', '0', '1'])]);
  assert.deepEqual(r.rows.map(r => r.share), [34, 33, 33, 0, 0]);
  assert.equal(r.rows[4].balance, 100);
});
test('空账本与已经平账无需转账', () => {
  assert.deepEqual(calculate([], []).transfers, []);
  assert.deepEqual(calculate(members, [bill(3, '0', ['0'])]).transfers, []);
});
test('拒绝无参与者、未知成员和无效整数金额', () => {
  for (const b of [bill(0, '0', ['0']), bill(1.1, '0', ['0']), bill(1, 'x', ['0']), bill(1, '0', []), bill(1, '0', ['x'])]) assert.throws(() => calculate(members, [b]));
});
test('1000 组随机账单：分摊守恒且执行转账后所有人净额为零', () => {
  let seed = 20261008;
  const rand = n => { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return seed % n; };
  for (let k = 0; k < 1000; k++) {
    const bills = Array.from({ length: 1 + rand(40) }, () => {
      const participants = members.filter(() => rand(3) !== 0).map(m => m.id);
      if (!participants.length) participants.push('0');
      return bill(1 + rand(1000000), String(rand(5)), participants);
    });
    const r = calculate(members, bills);
    assert.equal(r.rows.reduce((s, x) => s + x.share, 0), r.total);
    assert.equal(r.rows.reduce((s, x) => s + x.paid, 0), r.total);
    const balances = new Map(r.rows.map(x => [x.id, x.balance]));
    for (const t of r.transfers) {
      assert.ok(Number.isSafeInteger(t.cents) && t.cents > 0);
      balances.set(t.from, balances.get(t.from) + t.cents); balances.set(t.to, balances.get(t.to) - t.cents);
    }
    assert.ok([...balances.values()].every(v => v === 0));
    assert.ok(r.transfers.length <= members.length - 1);
  }
});
