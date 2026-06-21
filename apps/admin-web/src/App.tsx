import { Link, Route, Routes } from "react-router-dom";
import "./App.css";
import DashboardPage from "./pages/DashboardPage";
import MoviesPage from "./pages/MoviesPage";
import MovieDetailPage from "./pages/MovieDetailPage";
import TheatresPage from "./pages/TheatresPage";
import TheatreDetailPage from "./pages/TheatreDetailPage";
import ScreenDetailPage from "./pages/ScreenDetailPage";
import SeatLayoutEditorPage from "./pages/SeatLayoutEditorPage";

function App() {
  return (
    <div className="app">
      <header className="app-header">
        <Link to="/">MovieTicket Admin</Link>
        <nav>
          <Link to="/movies">Movies</Link>
          <Link to="/theatres">Theatres</Link>
        </nav>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/movies" element={<MoviesPage />} />
          <Route path="/movies/:movieId" element={<MovieDetailPage />} />
          <Route path="/theatres" element={<TheatresPage />} />
          <Route path="/theatres/:theatreId" element={<TheatreDetailPage />} />
          <Route path="/screens/:screenId" element={<ScreenDetailPage />} />
          <Route path="/screens/:screenId/seat-layouts/new" element={<SeatLayoutEditorPage />} />
          <Route path="/seat-layouts/:layoutId/edit" element={<SeatLayoutEditorPage />} />
        </Routes>
      </main>
    </div>
  );
}

export default App;
