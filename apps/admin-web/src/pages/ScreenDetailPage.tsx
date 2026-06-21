import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { Movie, SeatLayout, Showtime } from "../types";

export default function ScreenDetailPage() {
  const { screenId } = useParams<{ screenId: string }>();
  const navigate = useNavigate();

  const [layouts, setLayouts] = useState<SeatLayout[]>([]);
  const [showtimes, setShowtimes] = useState<Showtime[]>([]);
  const [movies, setMovies] = useState<Movie[]>([]);
  const [error, setError] = useState<string | null>(null);

  const [movieId, setMovieId] = useState("");
  const [startTime, setStartTime] = useState("");
  const [basePrice, setBasePrice] = useState("200");
  const [submitting, setSubmitting] = useState(false);

  function load() {
    if (!screenId) return;
    api.listSeatLayouts(screenId).then(setLayouts).catch((err) => setError(`Could not load seat layouts: ${err.message}`));
    api.listShowtimes(screenId).then(setShowtimes).catch((err) => setError(`Could not load showtimes: ${err.message}`));
  }

  useEffect(load, [screenId]);
  useEffect(() => {
    api.listMovies().then((result) => {
      setMovies(result);
      if (result.length > 0) setMovieId(result[0].id);
    });
  }, []);

  function handleCreateDraft() {
    // No empty-draft POST here: the backend's only way to add seats is
    // the initial POST /admin/seat-layouts/draft call itself (no "add
    // seat to an existing draft" endpoint exists, per §4.5's Builder
    // workflow -- the full flat list is built client-side first, then
    // saved once). The editor builds that list before ever calling the
    // API.
    if (!screenId) return;
    navigate(`/screens/${screenId}/seat-layouts/new`);
  }

  async function handleCreateShowtime(e: React.FormEvent) {
    e.preventDefault();
    if (!screenId) return;
    const movie = movies.find((m) => m.id === movieId);
    if (!movie) return;
    setSubmitting(true);
    setError(null);
    try {
      // movie_title is carried through from the dropdown selection here --
      // booking service has no live path to catalog (design v12/v16), so
      // it relies entirely on what this form submits at creation time.
      await api.createShowtime({
        movie_id: movie.id,
        movie_title: movie.title,
        screen_id: screenId,
        start_time: new Date(startTime).toISOString(),
        base_price: Number(basePrice),
      });
      load();
    } catch (err) {
      setError(`Could not create showtime: ${(err as Error).message}`);
    } finally {
      setSubmitting(false);
    }
  }

  async function handleActivate(showtimeId: string) {
    setError(null);
    try {
      await api.activateShowtime(showtimeId);
      load();
    } catch (err) {
      setError(`Could not activate showtime: ${(err as Error).message}`);
    }
  }

  async function handleDeactivate(showtimeId: string) {
    setError(null);
    try {
      await api.deactivateShowtime(showtimeId);
      load();
    } catch (err) {
      setError(`Could not deactivate showtime: ${(err as Error).message}`);
    }
  }

  return (
    <div>
      <h1>Screen</h1>
      {error && <div className="error-banner">{error}</div>}

      <h2>Seat layouts</h2>
      <table className="admin-table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Status</th>
            <th>Locked by</th>
          </tr>
        </thead>
        <tbody>
          {layouts.map((l) => (
            <tr key={l.id}>
              <td>
                <Link to={`/seat-layouts/${l.id}/edit`}>{l.name}</Link>
              </td>
              <td>{l.status}</td>
              <td>{l.locked_by_user_id ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <button className="secondary-button" onClick={handleCreateDraft} data-testid="new-draft-button">
        New seat layout draft
      </button>

      <h2>Showtimes</h2>
      <table className="admin-table">
        <thead>
          <tr>
            <th>Movie</th>
            <th>Start time</th>
            <th>Base price</th>
            <th>Status</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {showtimes.map((st) => (
            <tr key={st.id}>
              <td>{st.movie_title}</td>
              <td>{new Date(st.start_time).toLocaleString()}</td>
              <td>₹{st.base_price.toFixed(2)}</td>
              <td>
                <span className={`badge ${st.is_active ? "active" : "inactive"}`}>
                  {st.is_active ? "Active" : "Inactive"}
                </span>
              </td>
              <td>
                {st.is_active ? (
                  <button className="secondary-button" onClick={() => handleDeactivate(st.id)}>
                    Deactivate
                  </button>
                ) : (
                  <button className="secondary-button" onClick={() => handleActivate(st.id)}>
                    Activate
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <form className="admin-form" onSubmit={handleCreateShowtime}>
        <h3>Schedule a showtime</h3>
        <label>
          Movie
          <select data-testid="showtime-movie-select" value={movieId} onChange={(e) => setMovieId(e.target.value)}>
            {movies.map((m) => (
              <option key={m.id} value={m.id}>
                {m.title}
              </option>
            ))}
          </select>
        </label>
        <label>
          Start time
          <input
            data-testid="showtime-start-input"
            type="datetime-local"
            value={startTime}
            onChange={(e) => setStartTime(e.target.value)}
            required
          />
        </label>
        <label>
          Base price
          <input
            data-testid="showtime-price-input"
            type="number"
            step="0.01"
            value={basePrice}
            onChange={(e) => setBasePrice(e.target.value)}
            required
          />
        </label>
        <button
          className="primary-button"
          type="submit"
          disabled={submitting || !movieId}
          data-testid="create-showtime-button"
        >
          {submitting ? "Scheduling…" : !movieId ? "Loading movies…" : "Schedule showtime"}
        </button>
      </form>
    </div>
  );
}
