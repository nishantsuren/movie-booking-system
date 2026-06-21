import { Route, Routes } from "react-router-dom";
import "./App.css";
import BrowsePage from "./pages/BrowsePage";
import ShowtimesPage from "./pages/ShowtimesPage";
import SeatmapPage from "./pages/SeatmapPage";
import CheckoutPage from "./pages/CheckoutPage";
import ConfirmationPage from "./pages/ConfirmationPage";

function App() {
  return (
    <div className="app">
      <header className="app-header">
        <a href="/">MovieTicket</a>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<BrowsePage />} />
          <Route path="/movies/:movieId/showtimes" element={<ShowtimesPage />} />
          <Route path="/showtimes/:showtimeId/seatmap" element={<SeatmapPage />} />
          <Route path="/bookings/:bookingId/checkout" element={<CheckoutPage />} />
          <Route path="/bookings/:bookingId/confirmation" element={<ConfirmationPage />} />
        </Routes>
      </main>
    </div>
  );
}

export default App;
