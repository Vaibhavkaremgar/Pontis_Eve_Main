import React from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";

import LinkedInAuth from "./pages/LinkedInAuth";
import Onboarding from "./pages/Onboarding";
import Dashboard from "./pages/Dashboard";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<LinkedInAuth />} />
        <Route path="/auth/linkedin/callback" element={<LinkedInAuth />} />
        <Route path="/onboarding" element={<Onboarding />} />
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
