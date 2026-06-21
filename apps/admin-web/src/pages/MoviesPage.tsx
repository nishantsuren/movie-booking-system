import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { Movie } from "../types";

export default function MoviesPage() {
  const [movies, setMovies] = useState<Movie[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [language, setLanguage] = useState("");
  const [durationMinutes, setDurationMinutes] = useState("");
  const [submitting, setSubmitting] = useState(false);

  function load() {
    api.listMovies().then(setMovies).catch((err) => setError(`Could not load movies: ${err.message}`));
  }

  useEffect(load, []);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await api.createMovie({
        title,
        language: language || undefined,
        duration_minutes: durationMinutes ? Number(durationMinutes) : undefined,
      });
      setTitle("");
      setLanguage("");
      setDurationMinutes("");
      load();
    } catch (err) {
      setError(`Could not create movie: ${(err as Error).message}`);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div>
      <h1>Movies</h1>
      {error && <div className="error-banner">{error}</div>}

      <form className="admin-form" onSubmit={handleCreate}>
        <h2>Add a movie</h2>
        <label>
          Title
          <input data-testid="movie-title-input" value={title} onChange={(e) => setTitle(e.target.value)} required />
        </label>
        <label>
          Language
          <input value={language} onChange={(e) => setLanguage(e.target.value)} />
        </label>
        <label>
          Duration (minutes)
          <input type="number" value={durationMinutes} onChange={(e) => setDurationMinutes(e.target.value)} />
        </label>
        <button className="primary-button" type="submit" disabled={submitting} data-testid="create-movie-button">
          {submitting ? "Adding…" : "Add movie"}
        </button>
      </form>

      <table className="admin-table">
        <thead>
          <tr>
            <th>Title</th>
            <th>Language</th>
            <th>Duration</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {movies.map((movie) => (
            <tr key={movie.id}>
              <td>
                <Link to={`/movies/${movie.id}`}>{movie.title}</Link>
              </td>
              <td>{movie.language}</td>
              <td>{movie.duration_minutes ? `${movie.duration_minutes} min` : "—"}</td>
              <td>
                <span className={`badge ${movie.is_active ? "active" : "inactive"}`}>
                  {movie.is_active ? "Active" : "Inactive"}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
