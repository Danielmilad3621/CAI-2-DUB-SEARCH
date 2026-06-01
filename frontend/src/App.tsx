import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { DetailsPage } from './pages/DetailsPage'
import { HomePage } from './pages/HomePage'
import { ResultsPage } from './pages/ResultsPage'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/results" element={<Navigate to="/" replace />} />
        <Route path="/results/:jobId" element={<ResultsPage />} />
        <Route path="/flight/:jobId" element={<DetailsPage />} />
      </Routes>
    </BrowserRouter>
  )
}
