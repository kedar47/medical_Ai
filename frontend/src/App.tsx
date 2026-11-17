import { lazy, Suspense, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  IconButton,
  Typography,
} from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import { NavLink, Route, Routes, Link } from "react-router-dom";
import {
  Activity,
  AudioLines,
  ChevronLeft,
  ChevronRight,
  ClipboardList,
  FlaskConical,
  LayoutDashboard,
  Menu,
  PackageCheck,
  PhoneCall,
  Pill,
  Search,
  ShieldCheck,
  Stethoscope,
  X,
} from "lucide-react";
import { api } from "./api/client";
import { Loading } from "./components/common";
const Dashboard = lazy(() => import("./pages/Dashboard"));
const Voice = lazy(() => import("./pages/VoiceAssistant"));
const Medicines = lazy(() => import("./pages/Medicines"));
const Matching = lazy(() => import("./pages/Matching"));
const Sessions = lazy(() => import("./pages/Sessions"));
const Orders = lazy(() => import("./pages/Orders"));
const Calls = lazy(() => import("./pages/Calls"));
const Status = lazy(() => import("./pages/SystemStatus"));
const Demo = lazy(() => import("./pages/DemoTesting"));
export const navigation = [
  ["/", "Overview", LayoutDashboard],
  ["/voice", "AI Voice Assistant", AudioLines],
  ["/medicines", "Medicine Catalogue", Pill],
  ["/matching", "Medicine Matching", Search],
  ["/sessions", "Order Sessions", ClipboardList],
  ["/orders", "Orders", PackageCheck],
  ["/live-calls", "Live Calls", PhoneCall],
  ["/calls", "Call History", Activity],
  ["/status", "System Status", ShieldCheck],
  ["/testing", "Demo Testing", FlaskConical],
] as const;
export default function App() {
  const [collapsed, setCollapsed] = useState(false);
  const [mobile, setMobile] = useState(false);
  const health = useQuery({
    queryKey: ["health"],
    queryFn: ({ signal }) => api.health(signal),
    refetchInterval: 30000,
    refetchIntervalInBackground: false,
  });
  return (
    <Box className={`shell ${collapsed ? "collapsed" : ""}`}>
      <a href="#main" className="skip-link">
        Skip to content
      </a>
      {mobile && (
        <button
          className="nav-scrim"
          aria-label="Close navigation"
          onClick={() => setMobile(false)}
        />
      )}
      <aside className={`sidebar ${mobile ? "open" : ""}`}>
        <Box className="brand">
          <Box className="brand-icon">
            <Stethoscope size={23} />
          </Box>
          {!collapsed && (
            <Box>
              <Typography variant="h6">
                medora<span>®</span>
              </Typography>
              <Typography variant="caption">MEDICINE OPERATIONS</Typography>
            </Box>
          )}
          <IconButton
            className="mobile-close"
            aria-label="Close menu"
            onClick={() => setMobile(false)}
          >
            <X />
          </IconButton>
        </Box>
        <Typography className="nav-label">
          {collapsed ? "···" : "YOUR WORKSPACE"}
        </Typography>
        <nav aria-label="Primary navigation">
          {navigation.map(([path, title, Icon], i) => (
            <Box key={path}>
              {[2, 6, 8].includes(i) && <div className="nav-divider" />}
              <NavLink
                to={path}
                end={path === "/"}
                title={title}
                onClick={() => setMobile(false)}
              >
                <Icon size={19} />
                {!collapsed && <span>{title}</span>}
              </NavLink>
            </Box>
          ))}
        </nav>
        <Box className="sidebar-footer">
          {!collapsed && (
            <>
              <ShieldCheck size={22} />
              <Typography variant="body2">
                Built around safe confirmation.
              </Typography>
              <Typography variant="caption">
                Approved catalogue. Explicit choices.
              </Typography>
            </>
          )}
          <Button
            onClick={() => setCollapsed((v) => !v)}
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            sx={{ mt: 2, color: "inherit", minWidth: 30 }}
          >
            {collapsed ? <ChevronRight size={18} /> : <ChevronLeft size={18} />}
          </Button>
        </Box>
      </aside>
      <Box className="workspace">
        <header className="topbar">
          <Box className="inline">
            <IconButton
              aria-label="Open navigation"
              className="mobile-menu"
              onClick={() => setMobile(true)}
            >
              <Menu />
            </IconButton>
            <Typography variant="body2" color="text.secondary">
              Medicine workspace <span className="top-divider">/</span>{" "}
              <strong>Local demo</strong>
            </Typography>
          </Box>
          <Box className="inline">
            <Chip
              size="small"
              label={
                health.isPending
                  ? "Checking API"
                  : health.isError
                    ? "API unavailable"
                    : "API connected"
              }
              color={health.isSuccess ? "success" : "default"}
              variant="outlined"
            />
            <div className="avatar" aria-label="Local operator">
              OP
            </div>
          </Box>
        </header>
        <main id="main" tabIndex={-1}>
          <Suspense fallback={<Loading />}>
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/voice" element={<Voice />} />
              <Route path="/medicines" element={<Medicines />} />
              <Route path="/matching" element={<Matching />} />
              <Route path="/sessions" element={<Sessions />} />
              <Route path="/orders" element={<Orders />} />
              <Route path="/live-calls" element={<Calls live />} />
              <Route path="/calls" element={<Calls />} />
              <Route path="/status" element={<Status />} />
              <Route path="/testing" element={<Demo />} />
              <Route
                path="*"
                element={
                  <Alert severity="info">
                    Page not found. <Link to="/">Return to overview</Link>
                  </Alert>
                }
              />
            </Routes>
          </Suspense>
          <footer className="page-footer">
            <Typography variant="caption">
              Medora · Local medicine-ordering demo
            </Typography>
            <Typography variant="caption">
              Orders require pharmacy review. No medical advice.
            </Typography>
          </footer>
        </main>
      </Box>
    </Box>
  );
}
