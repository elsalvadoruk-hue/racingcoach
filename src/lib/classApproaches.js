// Share a completed call with already prepared audio before the next server frame.
export function createClassApproaches() {
  const receipts = new Map();
  const keyFor = (run, side, car) => JSON.stringify([run, side, car]);
  const lifetime = 12000;
  return {
    remember(run, notice) {
      if (!run || !notice?.cars?.length) return;
      const now = Date.now();
      for (const [key, at] of receipts) {
        if (at > now || now - at > lifetime) receipts.delete(key);
      }
      for (const car of notice.cars) receipts.set(keyFor(run, notice.side, car), now);
    },
    heard(run, notice) {
      if (!run || !notice?.cars?.length) return false;
      const now = Date.now();
      return notice.cars.every(car => {
        const at = receipts.get(keyFor(run, notice.side, car));
        return at !== undefined && now >= at && now - at <= lifetime;
      });
    },
  };
}
