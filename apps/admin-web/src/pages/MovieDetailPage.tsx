import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api/client";
import type { City, Movie, MovieRelease } from "../types";

export default function MovieDetailPage() {
  const { movieId } = useParams<{ movieId: string }>();
  const [movie, setMovie] = useState<Movie | null>(null);
  const [releases, setReleases] = useState<MovieRelease[]>([]);
  const [cities, setCities] = useState<City[]>([]);
  const [error, setError] = useState<string | null>(null);

  const [releaseCityId, setReleaseCityId] = useState("");
  const [releaseDate, setReleaseDate] = useState("");
  const [submitting, setSubmitting] = useState(false);

  function load() {
    if (!movieId) return;
    api.getMovie(movieId).then(setMovie).catch((err) => setError(`Could not load movie: ${err.message}`));
    api.listReleases(movieId).then(setReleases).catch((err) => setError(`Could not load releases: ${err.message}`));
  }

  useEffect(load, [movieId]);
  useEffect(() => {
    api.listCities().then((result) => {
      setCities(result);
      if (result.length > 0) setReleaseCityId(result[0].id);
    });
  }, []);

  async function toggleActive() {
    if (!movie) return;
    try {
      const updated = await api.updateMovie(movie.id, { is_active: !movie.is_active });
      setMovie(updated);
    } catch (err) {
      setError(`Could not update movie: ${(err as Error).message}`);
    }
  }

  async function handleAddRelease(e: React.FormEvent) {
    e.preventDefault();
    if (!movieId) return;
    setSubmitting(true);
    setError(null);
    try {
      await api.createRelease(movieId, { city_id: releaseCityId, release_date: releaseDate });
      setReleaseDate("");
      load();
    } catch (err) {
      setError(`Could not add release: ${(err as Error).message}`);
    } finally {
      setSubmitting(false);
    }
  }

  if (!movie) return <div>{error ? <div className="error-banner">{error}</div> : "Loading…"}</div>;

  const cityName = (cityId: string) => cities.find((c) => c.id === cityId)?.name ?? cityId;

  return (
    <div>
      <h1>{movie.title}</h1>
      {error && <div className="error-banner">{error}</div>}

      <p>
        <span className={`badge ${movie.is_active ? "active" : "inactive"}`}>
          {movie.is_active ? "Active" : "Inactive"}
        </span>{" "}
        <button className="secondary-button" onClick={toggleActive} data-testid="toggle-movie-active">
          {movie.is_active ? "Deactivate" : "Activate"}
        </button>
      </p>

      <h2>Releases</h2>
      <table className="admin-table">
        <thead>
          <tr>
            <th>City</th>
            <th>Release date</th>
            <th>Planned end</th>
          </tr>
        </thead>
        <tbody>
          {releases.map((r) => (
            <tr key={r.id}>
              <td>{cityName(r.city_id)}</td>
              <td>{r.release_date}</td>
              <td>{r.planned_end_date ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <form className="admin-form" onSubmit={handleAddRelease}>
        <h3>Add a release</h3>
        <label>
          City
          <select value={releaseCityId} onChange={(e) => setReleaseCityId(e.target.value)}>
            {cities.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Release date
          <input type="date" value={releaseDate} onChange={(e) => setReleaseDate(e.target.value)} required />
        </label>
        <button className="primary-button" type="submit" disabled={submitting || !releaseCityId}>
          {submitting ? "Adding…" : !releaseCityId ? "Loading cities…" : "Add release"}
        </button>
      </form>
    </div>
  );
}
