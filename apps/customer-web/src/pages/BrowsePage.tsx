import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { City, Movie } from "../types";

export default function BrowsePage() {
  const navigate = useNavigate();
  const [cities, setCities] = useState<City[]>([]);
  const [cityId, setCityId] = useState<string>("");
  const [movies, setMovies] = useState<Movie[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .listCities()
      .then((result) => {
        setCities(result);
        if (result.length > 0) setCityId(result[0].id);
      })
      .catch((err) => setError(`Could not load cities: ${err.message}`));
  }, []);

  useEffect(() => {
    if (!cityId) return;
    api
      .listMovies(cityId)
      .then(setMovies)
      .catch((err) => setError(`Could not load movies: ${err.message}`));
  }, [cityId]);

  return (
    <div>
      <h1>Now showing</h1>
      {error && <div className="error-banner">{error}</div>}

      <div className="city-picker">
        <label htmlFor="city-select">City: </label>
        <select id="city-select" value={cityId} onChange={(e) => setCityId(e.target.value)}>
          {cities.map((city) => (
            <option key={city.id} value={city.id}>
              {city.name}
            </option>
          ))}
        </select>
      </div>

      <div className="movie-grid">
        {movies.map((movie) => (
          <button
            key={movie.id}
            className="movie-card"
            onClick={() => navigate(`/movies/${movie.id}/showtimes?city=${cityId}`)}
          >
            <strong>{movie.title}</strong>
            <div>{movie.language}</div>
            <div>{movie.duration_minutes} min</div>
          </button>
        ))}
        {movies.length === 0 && !error && <p>No movies showing in this city right now.</p>}
      </div>
    </div>
  );
}
