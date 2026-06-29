import { useState } from "react";
import { Route, Routes } from "react-router-dom";
import "./App.css";
import BrowsePage from "./pages/BrowsePage";
import ShowtimesPage from "./pages/ShowtimesPage";
import SeatmapPage from "./pages/SeatmapPage";
import CheckoutPage from "./pages/CheckoutPage";
import ConfirmationPage from "./pages/ConfirmationPage";
import ChatWidget from "./components/ChatWidget/ChatWidget";

// Parsed once, straight off window.location.search rather than a
// react-router hook -- this only ever needs to run on the very first
// mount of a fresh page load (the one CheckoutPage's hard-reload
// hand-off-return redirect produces, see CheckoutPage.tsx), never on
// a later same-tab client-side navigation. Both params must be present
// together; a malformed/partial URL (e.g. someone navigating here
// directly) just falls back to completely normal behavior.
function parseResumeParams(): { sessionId: string; bookingId: string } | null {
  const params = new URLSearchParams(window.location.search);
  const sessionId = params.get("agent_session_id");
  const bookingId = params.get("agent_booking_id");
  return sessionId && bookingId ? { sessionId, bookingId } : null;
}

function App() {
  const [resumeParams] = useState(parseResumeParams);
  const [isChatOpen, setIsChatOpen] = useState(resumeParams !== null);

  return (
    <div className="app">
      <header className="app-header">
        <a href="/">MovieTicket</a>
        <button className="primary-button" onClick={() => setIsChatOpen(true)}>
          Book with AI
        </button>
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
      <ChatWidget
        isOpen={isChatOpen}
        onClose={() => setIsChatOpen(false)}
        resumeSessionId={resumeParams?.sessionId}
        resumeBookingId={resumeParams?.bookingId}
      />
    </div>
  );
}

export default App;
