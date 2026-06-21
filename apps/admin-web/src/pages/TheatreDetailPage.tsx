import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { Screen, Theatre } from "../types";

export default function TheatreDetailPage() {
  const { theatreId } = useParams<{ theatreId: string }>();
  const [theatre, setTheatre] = useState<Theatre | null>(null);
  const [screens, setScreens] = useState<Screen[]>([]);
  const [error, setError] = useState<string | null>(null);

  const [screenName, setScreenName] = useState("");
  const [submitting, setSubmitting] = useState(false);

  function load() {
    if (!theatreId) return;
    api.getTheatre(theatreId).then(setTheatre).catch((err) => setError(`Could not load theatre: ${err.message}`));
    api.listScreens(theatreId).then(setScreens).catch((err) => setError(`Could not load screens: ${err.message}`));
  }

  useEffect(load, [theatreId]);

  async function handleCreateScreen(e: React.FormEvent) {
    e.preventDefault();
    if (!theatreId) return;
    setSubmitting(true);
    setError(null);
    try {
      await api.createScreen(theatreId, { name: screenName });
      setScreenName("");
      load();
    } catch (err) {
      setError(`Could not create screen: ${(err as Error).message}`);
    } finally {
      setSubmitting(false);
    }
  }

  if (!theatre) return <div>{error ? <div className="error-banner">{error}</div> : "Loading…"}</div>;

  return (
    <div>
      <h1>{theatre.name}</h1>
      {error && <div className="error-banner">{error}</div>}

      <h2>Screens</h2>
      <table className="admin-table">
        <thead>
          <tr>
            <th>Name</th>
          </tr>
        </thead>
        <tbody>
          {screens.map((s) => (
            <tr key={s.id}>
              <td>
                <Link to={`/screens/${s.id}`}>{s.name}</Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <form className="admin-form" onSubmit={handleCreateScreen}>
        <h3>Add a screen</h3>
        <label>
          Name
          <input
            data-testid="screen-name-input"
            value={screenName}
            onChange={(e) => setScreenName(e.target.value)}
            required
          />
        </label>
        <button className="primary-button" type="submit" disabled={submitting} data-testid="create-screen-button">
          {submitting ? "Adding…" : "Add screen"}
        </button>
      </form>
    </div>
  );
}
