import { useEffect, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { Movie, ShowtimeListItem } from "../types";

function todayIsoDate(): string {
  return new Date().toISOString().slice(0, 10);
}

export default function ShowtimesPage() {
  const { movieId } = useParams<{ movieId: string }>();
  const [searchParams] = useSearchParams();
  const cityId = searchParams.get("city") ?? "";
  const navigate = useNavigate();

  const [date, setDate] = useState(searchParams.get("date") ?? todayIsoDate());
  const [movie, setMovie] = useState<Movie | null>(null);
  const [showtimes, setShowtimes] = useState<ShowtimeListItem[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!movieId || !cityId) return;
    setError(null);
    api
      .getMovieShowtimes(movieId, cityId, date)
      .then((result) => {
        setMovie(result.movie);
        setShowtimes(result.showtimes);
      })
      .catch((err) => setError(`Could not load showtimes: ${err.message}`));
  }, [movieId, cityId, date]);

  return (
    <div>
      <h1>{movie?.title ?? "Showtimes"}</h1>
      {error && <div className="error-banner">{error}</div>}

      <div className="city-picker">
        <label htmlFor="date-picker">Date: </label>
        <input id="date-picker" type="date" value={date} onChange={(e) => setDate(e.target.value)} />
      </div>

      <div className="showtime-list">
        {showtimes.map((st) => (
          <div key={st.id} className="showtime-row" onClick={() => navigate(`/showtimes/${st.id}/seatmap`)}>
            <span>
              {st.theatre_name} — {st.screen_name}
            </span>
            <span>{new Date(st.start_time).toLocaleString()}</span>
            <span>₹{st.base_price.toFixed(2)}</span>
          </div>
        ))}
        {showtimes.length === 0 && !error && <p>No showtimes for this date.</p>}
      </div>
    </div>
  );
}
