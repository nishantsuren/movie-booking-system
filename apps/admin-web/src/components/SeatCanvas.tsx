import { useRef, useState } from "react";
import type { MouseEvent as ReactMouseEvent } from "react";

export const PIXELS_PER_UNIT = 40;

export interface CanvasSeat {
  id: string;
  label: string;
  x: number;
  y: number;
  seat_type: string;
  is_active?: boolean;
}

interface RubberBand {
  startX: number;
  startY: number;
  x: number;
  y: number;
}

export default function SeatCanvas({
  seats,
  selectedIds,
  onToggleSelect,
  onRubberBandSelect,
  onCanvasClick,
  readOnly,
}: {
  seats: CanvasSeat[];
  selectedIds: Set<string>;
  onToggleSelect: (id: string, additive: boolean) => void;
  onRubberBandSelect: (ids: string[], additive: boolean) => void;
  onCanvasClick?: (x: number, y: number) => void;
  readOnly?: boolean;
}) {
  const surfaceRef = useRef<HTMLDivElement>(null);
  const [band, setBand] = useState<RubberBand | null>(null);

  const maxX = Math.max(0, ...seats.map((s) => s.x));
  const maxY = Math.max(0, ...seats.map((s) => s.y));
  const width = (maxX + 2) * PIXELS_PER_UNIT;
  const height = (maxY + 2) * PIXELS_PER_UNIT;

  function toWorld(clientX: number, clientY: number): { x: number; y: number } {
    const rect = surfaceRef.current!.getBoundingClientRect();
    return {
      x: Math.round(((clientX - rect.left) / PIXELS_PER_UNIT) * 100) / 100,
      y: Math.round(((clientY - rect.top) / PIXELS_PER_UNIT) * 100) / 100,
    };
  }

  function handleSurfaceMouseDown(e: ReactMouseEvent) {
    if (e.target !== surfaceRef.current) return; // clicked a seat, not empty canvas
    const { x, y } = toWorld(e.clientX, e.clientY);
    if (onCanvasClick) {
      onCanvasClick(x, y);
      return;
    }
    setBand({ startX: e.clientX, startY: e.clientY, x: e.clientX, y: e.clientY });
  }

  function handleSurfaceMouseMove(e: ReactMouseEvent) {
    if (!band) return;
    setBand({ ...band, x: e.clientX, y: e.clientY });
  }

  function handleSurfaceMouseUp() {
    if (!band) return;
    const left = Math.min(band.startX, band.x);
    const right = Math.max(band.startX, band.x);
    const top = Math.min(band.startY, band.y);
    const bottom = Math.max(band.startY, band.y);
    const rect = surfaceRef.current!.getBoundingClientRect();
    const ids = seats
      .filter((s) => {
        const px = rect.left + s.x * PIXELS_PER_UNIT + PIXELS_PER_UNIT / 2;
        const py = rect.top + s.y * PIXELS_PER_UNIT + PIXELS_PER_UNIT / 2;
        return px >= left && px <= right && py >= top && py <= bottom;
      })
      .map((s) => s.id);
    if (right - left > 4 || bottom - top > 4) {
      onRubberBandSelect(ids, false);
    }
    setBand(null);
  }

  return (
    <div className="canvas-wrapper">
      <div
        ref={surfaceRef}
        className="canvas-surface"
        style={{ width, height }}
        onMouseDown={handleSurfaceMouseDown}
        onMouseMove={handleSurfaceMouseMove}
        onMouseUp={handleSurfaceMouseUp}
        data-testid="canvas-surface"
      >
        {seats.map((seat) => (
          <button
            key={seat.id}
            type="button"
            className={`canvas-seat ${selectedIds.has(seat.id) ? "selected" : ""} ${seat.is_active === false ? "inactive" : ""}`}
            style={{ left: seat.x * PIXELS_PER_UNIT, top: seat.y * PIXELS_PER_UNIT }}
            data-testid={`canvas-seat-${seat.label}`}
            disabled={readOnly}
            onClick={(e) => onToggleSelect(seat.id, e.shiftKey)}
            title={`${seat.label} (${seat.seat_type})`}
          >
            {seat.label}
          </button>
        ))}
        {band && (
          <div
            className="rubber-band"
            style={{
              left: Math.min(band.startX, band.x) - surfaceRef.current!.getBoundingClientRect().left,
              top: Math.min(band.startY, band.y) - surfaceRef.current!.getBoundingClientRect().top,
              width: Math.abs(band.x - band.startX),
              height: Math.abs(band.y - band.startY),
            }}
          />
        )}
      </div>
    </div>
  );
}
