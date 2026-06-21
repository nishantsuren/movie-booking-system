// §4.5's Builder pattern: each tool invocation is a discrete
// construction step (addRow/addGrid/addCurve/addSingleSeat) appending to
// one in-progress client-side collection -- nothing about "rows" is ever
// sent to or stored by the server, only the resulting flat list. These
// are pure functions over that in-progress list so the canvas component
// stays a thin event-handling layer and the tools themselves are
// trivially unit-testable without any DOM/canvas involved.
export interface DraftSeat {
  id: string;
  label: string;
  x: number;
  y: number;
  seat_type: string;
  price_multiplier: number;
}

export interface PlacementOptions {
  seatType: string;
  priceMultiplier: number;
  labelPrefix: string;
}

function newId(): string {
  return crypto.randomUUID();
}

export function placeSingleSeat(x: number, y: number, label: string, opts: PlacementOptions): DraftSeat[] {
  return [{ id: newId(), label, x, y, seat_type: opts.seatType, price_multiplier: opts.priceMultiplier }];
}

/** A straight run of `count` seats from (x1,y1) to (x2,y2), evenly spaced. */
export function placeLine(x1: number, y1: number, x2: number, y2: number, count: number, opts: PlacementOptions): DraftSeat[] {
  if (count < 1) return [];
  if (count === 1) {
    return [{ id: newId(), label: `${opts.labelPrefix}1`, x: x1, y: y1, seat_type: opts.seatType, price_multiplier: opts.priceMultiplier }];
  }
  const seats: DraftSeat[] = [];
  for (let i = 0; i < count; i++) {
    const t = i / (count - 1);
    seats.push({
      id: newId(),
      label: `${opts.labelPrefix}${i + 1}`,
      x: x1 + (x2 - x1) * t,
      y: y1 + (y2 - y1) * t,
      seat_type: opts.seatType,
      price_multiplier: opts.priceMultiplier,
    });
  }
  return seats;
}

/** rows x cols block, labeled like conceptual rows (A1, A2, ... B1, B2,
 * ...) purely as a client-side label convenience -- never stored or
 * interpreted as a row by the server (§4.5). */
export function placeGrid(
  x: number,
  y: number,
  rows: number,
  cols: number,
  rowSpacing: number,
  colSpacing: number,
  opts: PlacementOptions,
): DraftSeat[] {
  const seats: DraftSeat[] = [];
  for (let r = 0; r < rows; r++) {
    // The row letter always varies by row -- labelPrefix is an optional
    // leading string (e.g. to distinguish two grid sections), never a
    // replacement for it, otherwise every row collides on the same label.
    const rowLabel = `${opts.labelPrefix}${String.fromCharCode(65 + r)}`;
    for (let c = 0; c < cols; c++) {
      seats.push({
        id: newId(),
        label: `${rowLabel}${c + 1}`,
        x: x + c * colSpacing,
        y: y + r * rowSpacing,
        seat_type: opts.seatType,
        price_multiplier: opts.priceMultiplier,
      });
    }
  }
  return seats;
}

/** `count` seats sampled along a quadratic bezier from (x1,y1) through
 * control point (cx,cy) to (x2,y2) -- the "curve" tool, for the
 * non-rectangular sections most real theatres actually have. */
export function placeCurve(
  x1: number,
  y1: number,
  cx: number,
  cy: number,
  x2: number,
  y2: number,
  count: number,
  opts: PlacementOptions,
): DraftSeat[] {
  if (count < 1) return [];
  const seats: DraftSeat[] = [];
  for (let i = 0; i < count; i++) {
    const t = count === 1 ? 0.5 : i / (count - 1);
    const x = (1 - t) * (1 - t) * x1 + 2 * (1 - t) * t * cx + t * t * x2;
    const y = (1 - t) * (1 - t) * y1 + 2 * (1 - t) * t * cy + t * t * y2;
    seats.push({ id: newId(), label: `${opts.labelPrefix}${i + 1}`, x, y, seat_type: opts.seatType, price_multiplier: opts.priceMultiplier });
  }
  return seats;
}
