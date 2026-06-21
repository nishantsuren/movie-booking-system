import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import { getAdminId } from "../lib/adminId";
import { placeCurve, placeGrid, placeLine, placeSingleSeat } from "../lib/placementTools";
import type { DraftSeat, PlacementOptions } from "../lib/placementTools";
import SeatCanvas from "../components/SeatCanvas";
import { ApiError } from "../types";
import type { SeatLayout, SeatTemplate } from "../types";

type Tool = "select" | "single" | "line" | "grid" | "curve";

// §4.6: heartbeat well under the ~2 minute staleness threshold so a
// healthy session never goes stale by accident.
const HEARTBEAT_INTERVAL_MS = 25_000;

interface LockInfo {
  locked_by_user_id: string | null;
  lock_acquired_at: string | null;
  lock_heartbeat_at: string | null;
}

export default function SeatLayoutEditorPage() {
  const { screenId, layoutId } = useParams<{ screenId?: string; layoutId?: string }>();
  const navigate = useNavigate();
  const isCreateMode = !layoutId;
  const adminId = getAdminId();

  const [draftName, setDraftName] = useState("New layout");
  const [draftSeats, setDraftSeats] = useState<DraftSeat[]>([]);
  const [creating, setCreating] = useState(false);

  const [layout, setLayout] = useState<SeatLayout | null>(null);
  const [lockState, setLockState] = useState<"acquiring" | "held" | "blocked" | "stale-rejected" | "released">("acquiring");
  const [lockInfo, setLockInfo] = useState<LockInfo | null>(null);
  const [publishing, setPublishing] = useState(false);
  const [published, setPublished] = useState(false);

  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [tool, setTool] = useState<Tool>("select");
  const [error, setError] = useState<string | null>(null);

  // Tool parameter form state -- plain numeric inputs, settable either by
  // typing directly or by clicking the canvas (for human convenience);
  // either way "Place" is an explicit, deterministic action, never an
  // auto-trigger after N clicks -- keeps this reliably scriptable too.
  const [seatType, setSeatType] = useState("STANDARD");
  const [priceMultiplier, setPriceMultiplier] = useState("1.0");
  const [labelPrefix, setLabelPrefix] = useState("");
  const [singleX, setSingleX] = useState("0");
  const [singleY, setSingleY] = useState("0");
  const [singleLabel, setSingleLabel] = useState("W1");
  const [lineX1, setLineX1] = useState("0");
  const [lineY1, setLineY1] = useState("0");
  const [lineX2, setLineX2] = useState("5");
  const [lineY2, setLineY2] = useState("0");
  const [lineCount, setLineCount] = useState("6");
  const [gridX, setGridX] = useState("0");
  const [gridY, setGridY] = useState("1");
  const [gridRows, setGridRows] = useState("5");
  const [gridCols, setGridCols] = useState("10");
  const [rowSpacing, setRowSpacing] = useState("1");
  const [colSpacing, setColSpacing] = useState("1");
  const [curveX1, setCurveX1] = useState("0");
  const [curveY1, setCurveY1] = useState("8");
  const [curveCx, setCurveCx] = useState("5");
  const [curveCy, setCurveCy] = useState("10");
  const [curveX2, setCurveX2] = useState("10");
  const [curveY2, setCurveY2] = useState("8");
  const [curveCount, setCurveCount] = useState("8");

  const opts: PlacementOptions = {
    seatType,
    priceMultiplier: Number(priceMultiplier) || 1,
    labelPrefix,
  };

  // --- load + lock (edit mode only) ---

  const load = useCallback(() => {
    if (!layoutId) return;
    api.getSeatLayout(layoutId).then(setLayout).catch((err) => setError(`Could not load layout: ${err.message}`));
  }, [layoutId]);

  const attemptLock = useCallback(() => {
    if (!layoutId) return;
    setLockState("acquiring");
    api
      .acquireLock(layoutId)
      .then((result) => {
        setLockState("held");
        setLockInfo(result);
        setLayout((prev) => (prev ? { ...prev, ...result } : prev));
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 409) {
          setLockState("blocked");
          const detail = (err.body as { detail?: LockInfo })?.detail;
          setLockInfo(detail ?? null);
        } else {
          setError(`Could not acquire edit lock: ${err.message}`);
        }
      });
  }, [layoutId]);

  const handleReleaseLock = useCallback(() => {
    if (!layoutId) return;
    api
      .releaseLock(layoutId)
      .then(() => setLockState("released"))
      .catch((err) => setError(`Could not release lock: ${err.message}`));
  }, [layoutId]);

  useEffect(() => {
    if (isCreateMode) return;
    load();
    attemptLock();
  }, [isCreateMode, load, attemptLock]);

  // Heartbeat while held -- refreshes lock_heartbeat_at so the session
  // never goes stale on its own (§4.6).
  const heartbeatRef = useRef<ReturnType<typeof setInterval> | null>(null);
  useEffect(() => {
    if (lockState !== "held" || !layoutId) {
      if (heartbeatRef.current) clearInterval(heartbeatRef.current);
      return;
    }
    heartbeatRef.current = setInterval(() => {
      api.acquireLock(layoutId).then(setLockInfo).catch(() => {
        // A heartbeat failure (e.g. lock lost to staleness already, or
        // genuinely taken over) surfaces on the next real edit attempt's
        // 403 instead of here -- avoids flapping the banner on a single
        // missed beat.
      });
    }, HEARTBEAT_INTERVAL_MS);
    return () => {
      if (heartbeatRef.current) clearInterval(heartbeatRef.current);
    };
  }, [lockState, layoutId]);

  useEffect(() => {
    return () => {
      if (lockState === "held" && layoutId) {
        api.releaseLock(layoutId).catch(() => undefined);
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // --- placement (create mode only) ---

  function appendSeats(newSeats: DraftSeat[]) {
    setDraftSeats((prev) => [...prev, ...newSeats]);
  }

  function handleCanvasClick(x: number, y: number) {
    if (tool === "single") {
      setSingleX(String(x));
      setSingleY(String(y));
    } else if (tool === "line") {
      setLineX1(String(x));
      setLineY1(String(y));
    } else if (tool === "grid") {
      setGridX(String(x));
      setGridY(String(y));
    } else if (tool === "curve") {
      setCurveX1(String(x));
      setCurveY1(String(y));
    }
  }

  function placeWithCurrentTool() {
    if (tool === "single") appendSeats(placeSingleSeat(Number(singleX), Number(singleY), singleLabel, opts));
    else if (tool === "line") appendSeats(placeLine(Number(lineX1), Number(lineY1), Number(lineX2), Number(lineY2), Number(lineCount), opts));
    else if (tool === "grid") appendSeats(placeGrid(Number(gridX), Number(gridY), Number(gridRows), Number(gridCols), Number(rowSpacing), Number(colSpacing), opts));
    else if (tool === "curve")
      appendSeats(placeCurve(Number(curveX1), Number(curveY1), Number(curveCx), Number(curveCy), Number(curveX2), Number(curveY2), Number(curveCount), opts));
  }

  function removeSelected() {
    setDraftSeats((prev) => prev.filter((s) => !selectedIds.has(s.id)));
    setSelectedIds(new Set());
  }

  async function handleSaveDraft() {
    if (!screenId) return;
    setCreating(true);
    setError(null);
    try {
      const created = await api.createDraft(
        screenId,
        draftName,
        draftSeats.map((s) => ({ id: s.id, label: s.label, x: s.x, y: s.y, seat_type: s.seat_type, price_multiplier: s.price_multiplier })),
      );
      navigate(`/seat-layouts/${created.id}/edit`);
    } catch (err) {
      setError(`Could not create draft: ${(err as Error).message}`);
    } finally {
      setCreating(false);
    }
  }

  // --- select + bulk edit (edit mode only) ---

  function toggleSelect(id: string, additive: boolean) {
    setSelectedIds((prev) => {
      const next = additive ? new Set(prev) : new Set<string>();
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function rubberBandSelect(ids: string[], additive: boolean) {
    setSelectedIds((prev) => {
      const next = additive ? new Set(prev) : new Set<string>();
      ids.forEach((id) => next.add(id));
      return next;
    });
  }

  async function handleBulkApply(fields: Partial<Pick<SeatTemplate, "seat_type" | "price_multiplier" | "is_active">>) {
    if (!layoutId || selectedIds.size === 0) return;
    setError(null);
    try {
      await api.bulkUpdateSeats(layoutId, Array.from(selectedIds), fields);
      load();
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) {
        setLockState("stale-rejected");
        setError("Your edit lock has gone stale -- reacquire before editing.");
      } else {
        setError(`Could not apply bulk edit: ${(err as Error).message}`);
      }
    }
  }

  async function handlePublish() {
    if (!layoutId) return;
    setPublishing(true);
    setError(null);
    try {
      await api.publishDraft(layoutId);
      setPublished(true);
      load();
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) {
        setLockState("stale-rejected");
        setError("Your edit lock has gone stale -- reacquire before publishing.");
      } else {
        setError(`Could not publish: ${(err as Error).message}`);
      }
    } finally {
      setPublishing(false);
    }
  }

  // --- render ---

  if (isCreateMode) {
    return (
      <div>
        <h1>New seat layout</h1>
        {error && <div className="error-banner">{error}</div>}
        <label>
          Name{" "}
          <input data-testid="draft-name-input" value={draftName} onChange={(e) => setDraftName(e.target.value)} />
        </label>

        <Toolbar
          tool={tool}
          setTool={setTool}
          seatType={seatType}
          setSeatType={setSeatType}
          priceMultiplier={priceMultiplier}
          setPriceMultiplier={setPriceMultiplier}
          labelPrefix={labelPrefix}
          setLabelPrefix={setLabelPrefix}
          singleX={singleX}
          setSingleX={setSingleX}
          singleY={singleY}
          setSingleY={setSingleY}
          singleLabel={singleLabel}
          setSingleLabel={setSingleLabel}
          lineX1={lineX1}
          setLineX1={setLineX1}
          lineY1={lineY1}
          setLineY1={setLineY1}
          lineX2={lineX2}
          setLineX2={setLineX2}
          lineY2={lineY2}
          setLineY2={setLineY2}
          lineCount={lineCount}
          setLineCount={setLineCount}
          gridX={gridX}
          setGridX={setGridX}
          gridY={gridY}
          setGridY={setGridY}
          gridRows={gridRows}
          setGridRows={setGridRows}
          gridCols={gridCols}
          setGridCols={setGridCols}
          rowSpacing={rowSpacing}
          setRowSpacing={setRowSpacing}
          colSpacing={colSpacing}
          setColSpacing={setColSpacing}
          curveX1={curveX1}
          setCurveX1={setCurveX1}
          curveY1={curveY1}
          setCurveY1={setCurveY1}
          curveCx={curveCx}
          setCurveCx={setCurveCx}
          curveCy={curveCy}
          setCurveCy={setCurveCy}
          curveX2={curveX2}
          setCurveX2={setCurveX2}
          curveY2={curveY2}
          setCurveY2={setCurveY2}
          curveCount={curveCount}
          setCurveCount={setCurveCount}
          onPlace={placeWithCurrentTool}
        />

        <p data-testid="seat-count">{draftSeats.length} seats placed</p>
        {selectedIds.size > 0 && (
          <button className="danger-button" onClick={removeSelected} data-testid="remove-selected-button">
            Remove {selectedIds.size} selected
          </button>
        )}

        <SeatCanvas
          seats={draftSeats}
          selectedIds={selectedIds}
          onToggleSelect={toggleSelect}
          onRubberBandSelect={rubberBandSelect}
          onCanvasClick={tool === "select" ? undefined : handleCanvasClick}
        />

        <button
          className="primary-button"
          disabled={creating || draftSeats.length === 0}
          onClick={handleSaveDraft}
          data-testid="save-draft-button"
        >
          {creating ? "Saving…" : "Save draft"}
        </button>
      </div>
    );
  }

  // --- edit mode ---

  if (!layout) {
    return <div>{error ? <div className="error-banner">{error}</div> : "Loading…"}</div>;
  }

  const iHoldLock = lockState === "held";
  const blockedByOther = lockState === "blocked" && lockInfo?.locked_by_user_id && lockInfo.locked_by_user_id !== adminId;

  return (
    <div>
      <h1>{layout.name}</h1>
      {error && <div className="error-banner" data-testid="editor-error">{error}</div>}

      {iHoldLock && (
        <div className="lock-banner held-by-me" data-testid="lock-banner-held">
          You hold the edit lock.
          <button className="secondary-button" onClick={handleReleaseLock} data-testid="release-lock-button">
            Release lock
          </button>
        </div>
      )}
      {lockState === "released" && (
        <div className="lock-banner held-by-other" data-testid="lock-banner-released">
          You released the edit lock. Another admin can now acquire it.
        </div>
      )}
      {blockedByOther && (
        <div className="lock-banner held-by-other" data-testid="lock-banner-blocked">
          Locked by admin {lockInfo!.locked_by_user_id} (acquired {lockInfo!.lock_acquired_at}, last heartbeat{" "}
          {lockInfo!.lock_heartbeat_at}). You can't edit until they release it or it goes stale (~2 minutes of
          silence).
          <button className="secondary-button" onClick={attemptLock} data-testid="retry-lock-button">
            Try again
          </button>
        </div>
      )}
      {lockState === "stale-rejected" && (
        <div className="lock-banner held-by-other" data-testid="lock-banner-stale">
          Your edit lock has gone stale.
          <button className="secondary-button" onClick={attemptLock} data-testid="reacquire-lock-button">
            Reacquire lock
          </button>
        </div>
      )}

      <p>
        Status: {layout.status} {published && "— published!"}
      </p>

      <SeatCanvas
        seats={layout.seats.map((s) => ({ id: s.id, label: s.label, x: s.position_x, y: s.position_y, seat_type: s.seat_type, is_active: s.is_active }))}
        selectedIds={selectedIds}
        onToggleSelect={toggleSelect}
        onRubberBandSelect={rubberBandSelect}
        readOnly={!iHoldLock}
      />

      {selectedIds.size > 0 && iHoldLock && (
        <BulkEditPanel count={selectedIds.size} onApply={handleBulkApply} />
      )}

      <button
        className="primary-button"
        disabled={!iHoldLock || publishing || layout.status !== "DRAFT"}
        onClick={handlePublish}
        data-testid="publish-button"
      >
        {publishing ? "Publishing…" : "Publish"}
      </button>
    </div>
  );
}

function BulkEditPanel({
  count,
  onApply,
}: {
  count: number;
  onApply: (fields: Partial<Pick<SeatTemplate, "seat_type" | "price_multiplier" | "is_active">>) => void;
}) {
  const [seatType, setSeatType] = useState("");
  const [priceMultiplier, setPriceMultiplier] = useState("");

  return (
    <div className="bulk-edit-panel" data-testid="bulk-edit-panel">
      <strong>{count} seats selected</strong>
      <label>
        Seat type
        <input value={seatType} onChange={(e) => setSeatType(e.target.value)} placeholder="(unchanged)" />
      </label>
      <label>
        Price multiplier
        <input value={priceMultiplier} onChange={(e) => setPriceMultiplier(e.target.value)} placeholder="(unchanged)" />
      </label>
      <button
        className="primary-button"
        data-testid="bulk-apply-button"
        onClick={() =>
          onApply({
            ...(seatType ? { seat_type: seatType } : {}),
            ...(priceMultiplier ? { price_multiplier: Number(priceMultiplier) } : {}),
          })
        }
      >
        Apply
      </button>
      <button
        className="secondary-button"
        data-testid="bulk-deactivate-button"
        onClick={() => onApply({ is_active: false })}
      >
        Deactivate selected
      </button>
    </div>
  );
}

interface ToolbarProps {
  tool: Tool;
  setTool: (t: Tool) => void;
  seatType: string;
  setSeatType: (v: string) => void;
  priceMultiplier: string;
  setPriceMultiplier: (v: string) => void;
  labelPrefix: string;
  setLabelPrefix: (v: string) => void;
  singleX: string;
  setSingleX: (v: string) => void;
  singleY: string;
  setSingleY: (v: string) => void;
  singleLabel: string;
  setSingleLabel: (v: string) => void;
  lineX1: string;
  setLineX1: (v: string) => void;
  lineY1: string;
  setLineY1: (v: string) => void;
  lineX2: string;
  setLineX2: (v: string) => void;
  lineY2: string;
  setLineY2: (v: string) => void;
  lineCount: string;
  setLineCount: (v: string) => void;
  gridX: string;
  setGridX: (v: string) => void;
  gridY: string;
  setGridY: (v: string) => void;
  gridRows: string;
  setGridRows: (v: string) => void;
  gridCols: string;
  setGridCols: (v: string) => void;
  rowSpacing: string;
  setRowSpacing: (v: string) => void;
  colSpacing: string;
  setColSpacing: (v: string) => void;
  curveX1: string;
  setCurveX1: (v: string) => void;
  curveY1: string;
  setCurveY1: (v: string) => void;
  curveCx: string;
  setCurveCx: (v: string) => void;
  curveCy: string;
  setCurveCy: (v: string) => void;
  curveX2: string;
  setCurveX2: (v: string) => void;
  curveY2: string;
  setCurveY2: (v: string) => void;
  curveCount: string;
  setCurveCount: (v: string) => void;
  onPlace: () => void;
}

function Toolbar(props: ToolbarProps) {
  const { tool, setTool, seatType, setSeatType, priceMultiplier, setPriceMultiplier, labelPrefix, setLabelPrefix, onPlace } = props;
  return (
    <div>
      <div className="editor-toolbar">
        {(["select", "single", "line", "grid", "curve"] as Tool[]).map((t) => (
          <button
            key={t}
            type="button"
            className={tool === t ? "active" : "secondary-button"}
            data-testid={`tool-${t}`}
            onClick={() => setTool(t)}
          >
            {t}
          </button>
        ))}
        <label>
          Type <input value={seatType} onChange={(e) => setSeatType(e.target.value)} style={{ width: 90 }} />
        </label>
        <label>
          Price × <input value={priceMultiplier} onChange={(e) => setPriceMultiplier(e.target.value)} style={{ width: 60 }} />
        </label>
        {tool !== "select" && tool !== "single" && (
          <label>
            Prefix <input data-testid="label-prefix-input" value={labelPrefix} onChange={(e) => setLabelPrefix(e.target.value)} style={{ width: 50 }} />
          </label>
        )}
      </div>

      {tool === "single" && (
        <div className="editor-toolbar">
          <label>
            X <input data-testid="single-x" value={props.singleX} onChange={(e) => props.setSingleX(e.target.value)} style={{ width: 60 }} />
          </label>
          <label>
            Y <input data-testid="single-y" value={props.singleY} onChange={(e) => props.setSingleY(e.target.value)} style={{ width: 60 }} />
          </label>
          <label>
            Label <input data-testid="single-label" value={props.singleLabel} onChange={(e) => props.setSingleLabel(e.target.value)} style={{ width: 70 }} />
          </label>
          <button className="primary-button" data-testid="place-single-button" onClick={onPlace}>
            Place seat
          </button>
        </div>
      )}

      {tool === "line" && (
        <div className="editor-toolbar">
          <label>
            X1 <input data-testid="line-x1" value={props.lineX1} onChange={(e) => props.setLineX1(e.target.value)} style={{ width: 50 }} />
          </label>
          <label>
            Y1 <input data-testid="line-y1" value={props.lineY1} onChange={(e) => props.setLineY1(e.target.value)} style={{ width: 50 }} />
          </label>
          <label>
            X2 <input data-testid="line-x2" value={props.lineX2} onChange={(e) => props.setLineX2(e.target.value)} style={{ width: 50 }} />
          </label>
          <label>
            Y2 <input data-testid="line-y2" value={props.lineY2} onChange={(e) => props.setLineY2(e.target.value)} style={{ width: 50 }} />
          </label>
          <label>
            Count <input data-testid="line-count" value={props.lineCount} onChange={(e) => props.setLineCount(e.target.value)} style={{ width: 50 }} />
          </label>
          <button className="primary-button" data-testid="place-line-button" onClick={onPlace}>
            Place line
          </button>
        </div>
      )}

      {tool === "grid" && (
        <div className="editor-toolbar">
          <label>
            X <input data-testid="grid-x" value={props.gridX} onChange={(e) => props.setGridX(e.target.value)} style={{ width: 50 }} />
          </label>
          <label>
            Y <input data-testid="grid-y" value={props.gridY} onChange={(e) => props.setGridY(e.target.value)} style={{ width: 50 }} />
          </label>
          <label>
            Rows <input data-testid="grid-rows" value={props.gridRows} onChange={(e) => props.setGridRows(e.target.value)} style={{ width: 50 }} />
          </label>
          <label>
            Cols <input data-testid="grid-cols" value={props.gridCols} onChange={(e) => props.setGridCols(e.target.value)} style={{ width: 50 }} />
          </label>
          <label>
            Row spacing <input data-testid="grid-row-spacing" value={props.rowSpacing} onChange={(e) => props.setRowSpacing(e.target.value)} style={{ width: 50 }} />
          </label>
          <label>
            Col spacing <input data-testid="grid-col-spacing" value={props.colSpacing} onChange={(e) => props.setColSpacing(e.target.value)} style={{ width: 50 }} />
          </label>
          <button className="primary-button" data-testid="place-grid-button" onClick={onPlace}>
            Place grid
          </button>
        </div>
      )}

      {tool === "curve" && (
        <div className="editor-toolbar">
          <label>
            X1 <input data-testid="curve-x1" value={props.curveX1} onChange={(e) => props.setCurveX1(e.target.value)} style={{ width: 50 }} />
          </label>
          <label>
            Y1 <input data-testid="curve-y1" value={props.curveY1} onChange={(e) => props.setCurveY1(e.target.value)} style={{ width: 50 }} />
          </label>
          <label>
            CX <input data-testid="curve-cx" value={props.curveCx} onChange={(e) => props.setCurveCx(e.target.value)} style={{ width: 50 }} />
          </label>
          <label>
            CY <input data-testid="curve-cy" value={props.curveCy} onChange={(e) => props.setCurveCy(e.target.value)} style={{ width: 50 }} />
          </label>
          <label>
            X2 <input data-testid="curve-x2" value={props.curveX2} onChange={(e) => props.setCurveX2(e.target.value)} style={{ width: 50 }} />
          </label>
          <label>
            Y2 <input data-testid="curve-y2" value={props.curveY2} onChange={(e) => props.setCurveY2(e.target.value)} style={{ width: 50 }} />
          </label>
          <label>
            Count <input data-testid="curve-count" value={props.curveCount} onChange={(e) => props.setCurveCount(e.target.value)} style={{ width: 50 }} />
          </label>
          <button className="primary-button" data-testid="place-curve-button" onClick={onPlace}>
            Place curve
          </button>
        </div>
      )}
    </div>
  );
}
