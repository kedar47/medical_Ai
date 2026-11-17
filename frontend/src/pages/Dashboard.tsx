import { Box, Button, Typography } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowUpRight,
  ArrowRight,
  PackageCheck,
  Pill,
  PhoneCall,
  Layers3,
} from "lucide-react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import {
  DataTable,
  Empty,
  Failure,
  Header,
  Loading,
  Panel,
  Status,
  date,
  phone,
} from "../components/common";
export default function Dashboard() {
  const stats = useQuery({
    queryKey: ["stats"],
    queryFn: ({ signal }) => api.stats(signal),
  });
  const orders = useQuery({
    queryKey: ["orders", 0],
    queryFn: ({ signal }) => api.orders(0, signal),
  });
  const voice = useQuery({
    queryKey: ["voice-status"],
    queryFn: ({ signal }) => api.voiceStatus(signal),
  });
  const tel = useQuery({
    queryKey: ["telephony"],
    queryFn: ({ signal }) => api.telephony(signal),
  });
  const metrics = stats.data
    ? ([
        ["Approved medicines", stats.data.medicines, Pill],
        ["Confirmed orders", stats.data.orders, PackageCheck],
        ["Active drafts", stats.data.active_sessions, Layers3],
        ["Recorded calls", stats.data.calls, PhoneCall],
      ] as const)
    : [];
  return (
    <>
      <Header
        eyebrow="YOUR OPERATIONS, AT A GLANCE"
        title="A clearer view of every order."
        description="One workspace for your catalogue, conversations and pharmacy review queue."
        action={
          <Button
            component={Link}
            to="/voice"
            variant="contained"
            endIcon={<ArrowUpRight size={18} />}
          >
            Start a conversation
          </Button>
        }
      />
      <Box className="hero">
        <Box>
          <Typography className="eyebrow">AI MEDICINE ASSISTANT</Typography>
          <Typography variant="h5">
            A conversation. A carefully confirmed order.
          </Typography>
          <Typography mt={1}>
            Hindi, English and Hinglish voice ordering, backed by your approved
            catalogue.
          </Typography>
          <Button
            component={Link}
            to="/sessions"
            endIcon={<ArrowRight size={17} />}
            sx={{ mt: 2, color: "white", borderColor: "#ffffff70" }}
            variant="outlined"
          >
            Create a manual order
          </Button>
        </Box>
        <Box className="hero-symbol" aria-hidden="true">
          <Pill size={64} strokeWidth={1} />
        </Box>
      </Box>
      {stats.isPending ? (
        <Loading />
      ) : stats.isError ? (
        <Failure error={stats.error} retry={() => stats.refetch()} />
      ) : (
        <Box className="metrics">
          {metrics.map(([label, count, Icon]) => (
            <Panel key={label}>
              <Box className="metric-top">
                <Typography color="text.secondary" variant="body2">
                  {label}
                </Typography>
                <Icon size={19} />
              </Box>
              <Typography variant="h3" mt={2}>
                {count}
              </Typography>
              <Typography variant="caption" color="text.secondary">
                From persisted backend data
              </Typography>
            </Panel>
          ))}
        </Box>
      )}
      <Box className="two-col">
        <Panel
          title="Recent orders"
          action={
            <Button component={Link} to="/orders" size="small">
              View all <ArrowUpRight size={15} />
            </Button>
          }
        >
          {orders.isPending ? (
            <Loading />
          ) : orders.isError ? (
            <Failure error={orders.error} retry={() => orders.refetch()} />
          ) : !orders.data.items.length ? (
            <Empty text="Confirmed orders will appear here after customer approval." />
          ) : (
            <DataTable
              columns={["Order", "Customer", "Created", "Status"]}
              rows={orders.data.items.slice(0, 5).map((o) => [
                <Typography variant="body2" fontFamily="monospace">
                  {o.order_id.slice(-10)}
                </Typography>,
                o.customer_name || phone(o.customer_phone),
                date(o.created_at),
                <Status value={o.status} />,
              ])}
            />
          )}
        </Panel>
        <Panel title="Workspace status">
          <Box className="status-list">
            <Box>
              <Typography>Voice engine</Typography>
              {voice.isError ? (
                <Failure error={voice.error} />
              ) : (
                <Status
                  value={
                    voice.data
                      ? voice.data.enabled &&
                        voice.data.stt.ready &&
                        voice.data.tts.ready
                        ? "Configured"
                        : "Unavailable"
                      : "Loading"
                  }
                />
              )}
            </Box>
            <Box>
              <Typography>Telephone integration</Typography>
              <Status
                value={
                  tel.data?.simulated
                    ? "MOCK / SIMULATION"
                    : tel.data?.enabled
                      ? "Verification pending"
                      : "Disabled"
                }
              />
            </Box>
            <Box>
              <Typography>Active calls</Typography>
              <strong>{stats.data?.active_calls ?? "—"}</strong>
            </Box>
          </Box>
          <Typography color="text.secondary" variant="body2" mt={3}>
            Telephone activation is unverified. Local voice processing can take
            27–41 seconds.
          </Typography>
          <Button component={Link} to="/status" size="small" sx={{ mt: 2 }}>
            Inspect system status <ArrowRight size={15} />
          </Button>
        </Panel>
      </Box>
    </>
  );
}
