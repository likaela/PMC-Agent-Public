/** A gesture consumes one page only, including its inertial tail and reversals. */
export function createWheelGate(quietMs = 260, threshold = 40) {
  let lastAt = -Infinity;
  let consumed = false;
  let distance = 0;
  let direction = 0;
  let lockedUntil = 0;
  return {
    lock(until: number) {
      lockedUntil = until;
      consumed = true;
    },
    reset() {
      lastAt = -Infinity;
      consumed = false;
      distance = 0;
      direction = 0;
      lockedUntil = 0;
    },
    feed(delta: number, at: number): number {
      if (at - lastAt > quietMs) {
        consumed = false;
        distance = 0;
        direction = 0;
      }
      lastAt = at;
      if (at < lockedUntil) {
        consumed = true;
        return 0;
      }
      if (consumed || !Number.isFinite(delta) || delta === 0) return 0;
      const sign = Math.sign(delta);
      if (direction && sign !== direction) distance = 0;
      direction = sign;
      distance += Math.min(Math.abs(delta), 100);
      if (distance < threshold) return 0;
      consumed = true;
      return sign;
    },
  };
}
