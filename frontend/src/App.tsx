import { Routes, Route, Navigate } from 'react-router-dom';
import Upload from './pages/Upload';
import Job from './pages/Job';
import Review from './pages/Review';
import Catalog from './pages/Catalog';

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/upload" replace />} />
      <Route path="/upload" element={<Upload />} />
      <Route path="/jobs/:jobId" element={<Job />} />
      <Route path="/jobs/:jobId/review" element={<Review />} />
      <Route path="/catalog" element={<Catalog />} />
    </Routes>
  );
}
