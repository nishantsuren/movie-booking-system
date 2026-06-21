import { Link } from "react-router-dom";

export default function DashboardPage() {
  return (
    <div>
      <h1>Admin dashboard</h1>
      <p>
        <Link to="/movies">Manage movies</Link>
      </p>
      <p>
        <Link to="/theatres">Manage theatres, screens, seat layouts, and showtimes</Link>
      </p>
    </div>
  );
}
