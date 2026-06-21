import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { City, Theatre } from "../types";

export default function TheatresPage() {
  const [theatres, setTheatres] = useState<Theatre[]>([]);
  const [cities, setCities] = useState<City[]>([]);
  const [error, setError] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [cityId, setCityId] = useState("");
  const [submitting, setSubmitting] = useState(false);

  function load() {
    api.listTheatres().then(setTheatres).catch((err) => setError(`Could not load theatres: ${err.message}`));
  }

  useEffect(load, []);
  useEffect(() => {
    api.listCities().then((result) => {
      setCities(result);
      if (result.length > 0) setCityId(result[0].id);
    });
  }, []);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await api.createTheatre({ city_id: cityId, name });
      setName("");
      load();
    } catch (err) {
      setError(`Could not create theatre: ${(err as Error).message}`);
    } finally {
      setSubmitting(false);
    }
  }

  const cityName = (id: string) => cities.find((c) => c.id === id)?.name ?? id;

  return (
    <div>
      <h1>Theatres</h1>
      {error && <div className="error-banner">{error}</div>}

      <form className="admin-form" onSubmit={handleCreate}>
        <h2>Add a theatre</h2>
        <label>
          City
          <select data-testid="theatre-city-select" value={cityId} onChange={(e) => setCityId(e.target.value)}>
            {cities.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Name
          <input data-testid="theatre-name-input" value={name} onChange={(e) => setName(e.target.value)} required />
        </label>
        <button
          className="primary-button"
          type="submit"
          disabled={submitting || !cityId}
          data-testid="create-theatre-button"
        >
          {submitting ? "Adding…" : !cityId ? "Loading cities…" : "Add theatre"}
        </button>
      </form>

      <table className="admin-table">
        <thead>
          <tr>
            <th>Name</th>
            <th>City</th>
          </tr>
        </thead>
        <tbody>
          {theatres.map((t) => (
            <tr key={t.id}>
              <td>
                <Link to={`/theatres/${t.id}`}>{t.name}</Link>
              </td>
              <td>{cityName(t.city_id)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
