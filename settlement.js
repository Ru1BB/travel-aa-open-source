export function parseAmount(value) {
  const text = String(value).trim();
  if (!/^\d+(\.\d{1,2})?$/.test(text)) throw new Error('请输入最多两位小数的正数金额');
  const [whole, fraction = ''] = text.split('.');
  const cents = Number(whole) * 100 + Number(fraction.padEnd(2, '0'));
  if (!Number.isSafeInteger(cents) || cents <= 0 || cents > 10000000000) throw new Error('金额须大于 0，且不超过一亿元');
  return cents;
}
export const money = cents => `${cents < 0 ? '-' : ''}${Math.floor(Math.abs(cents) / 100)}.${String(Math.abs(cents) % 100).padStart(2, '0')}`;
export function calculate(members, bills) {
  const rows = members.map(m => ({ ...m, paid: 0, share: 0, balance: 0 }));
  const lookup = new Map(rows.map(r => [r.id, r]));
  let total = 0;
  for (const bill of bills) {
    const payer = lookup.get(bill.payer);
    const selected = new Set(bill.participants);
    const participants = rows.filter(r => selected.has(r.id));
    if (!payer || !participants.length || participants.length !== selected.size || !Number.isSafeInteger(bill.cents) || bill.cents <= 0) throw new Error('账单数据无效');
    total += bill.cents;
    if (!Number.isSafeInteger(total)) throw new Error('账单总金额超出计算范围');
    payer.paid += bill.cents;
    const base = Math.floor(bill.cents / participants.length), remainder = bill.cents % participants.length;
    participants.forEach((r, i) => { r.share += base + (i < remainder ? 1 : 0); });
  }
  rows.forEach(r => { r.balance = r.paid - r.share; });
  const creditors = rows.filter(r => r.balance > 0).map(r => ({ id: r.id, remaining: r.balance }));
  const debtors = rows.filter(r => r.balance < 0).map(r => ({ id: r.id, remaining: -r.balance }));
  const transfers = [];
  let i = 0, j = 0;
  while (i < debtors.length && j < creditors.length) {
    const cents = Math.min(debtors[i].remaining, creditors[j].remaining);
    transfers.push({ from: debtors[i].id, to: creditors[j].id, cents });
    debtors[i].remaining -= cents; creditors[j].remaining -= cents;
    if (!debtors[i].remaining) i++;
    if (!creditors[j].remaining) j++;
  }
  return { total, rows, transfers };
}
