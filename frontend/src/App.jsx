import { Route, Routes } from "react-router-dom";
import Layout from "./components/Layout.jsx";
import Landing from "./pages/Landing.jsx";
import Dashboard from "./pages/Dashboard.jsx";
import CreateWorkspace from "./pages/CreateWorkspace.jsx";
import Resources from "./pages/Resources.jsx";
import Processing from "./pages/Processing.jsx";
import Report from "./pages/Report.jsx";
import Chat from "./pages/Chat.jsx";

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route path="/app" element={<Layout />}>
        <Route index element={<Dashboard />} />
        <Route path="new" element={<CreateWorkspace />} />
        <Route path="w/:id" element={<Resources />} />
        <Route path="w/:id/process" element={<Processing />} />
        <Route path="w/:id/report" element={<Report />} />
        <Route path="w/:id/chat" element={<Chat />} />
      </Route>
      <Route path="*" element={<Landing />} />
    </Routes>
  );
}
