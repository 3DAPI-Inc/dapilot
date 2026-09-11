import { Routes, Route } from 'react-router-dom';
import Landing from './pages/Landing';
import Upload from './pages/Upload';
import AgentRuns from './pages/AgentRuns';
import Job from './pages/Job';
import Review from './pages/Review';
import Catalog from './pages/Catalog';
import Studio from './pages/Studio';

export default function App() {
  return (
    <Routes>
      {/* "/" was a redirect to /upload while there was nothing to land on. */}
      <Route path="/" element={<Landing />} />
      <Route path="/upload" element={<Upload />} />
      <Route path="/agent-runs" element={<AgentRuns />} />
      <Route path="/jobs/:jobId" element={<Job />} />
      <Route path="/jobs/:jobId/review" element={<Review />} />
      <Route path="/catalog" element={<Catalog />} />
      <Route path="/studio" element={<Studio />} />
    </Routes>
  );
}
