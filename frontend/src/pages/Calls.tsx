import { useEffect, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Divider,
  Drawer,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import {
  DataTable,
  Empty,
  Failure,
  Header,
  Loading,
  Pager,
  Panel,
  Status,
  date,
  phone,
} from "../components/common";

function elapsed(startedAt: string, endedAt: string | null, now: number) {
  if (!startedAt) return "—";
  const end = endedAt ? Date.parse(endedAt) : now;
  const seconds = Math.max(0, Math.round((end - Date.parse(startedAt)) / 1000));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

export default function Calls({ live = false }: { live?: boolean }) {
  const [offset, setOffset] = useState(0);
  const [status, setStatus] = useState("");
  const [id, setId] = useState<string | null>(null);
  const [destination, setDestination] = useState("");
  const [language, setLanguage] = useState<"hi" | "en">("hi");
  const [now, setNow] = useState(Date.now());
  const cache = useQueryClient();
  useEffect(() => {
    if (!live) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [live]);
  const dial = useMutation({
    mutationFn: () => api.dialCall(destination.trim(), language),
    onSuccess: () => {
      cache.invalidateQueries({ queryKey: ["calls"] });
      cache.invalidateQueries({ queryKey: ["stats"] });
    },
  });
  const tel = useQuery({
    queryKey: ["telephony"],
    queryFn: ({ signal }) => api.telephony(signal),
    refetchInterval: live ? 15000 : false,
  });
  const query = useQuery({
    queryKey: ["calls", offset, status, live],
    queryFn: ({ signal }) => api.calls(offset, status, live, signal),
    refetchInterval: live ? 5000 : false,
    refetchIntervalInBackground: false,
  });
  const detail = useQuery({
    queryKey: ["call", id],
    queryFn: ({ signal }) => api.call(id!, signal),
    enabled: !!id,
    refetchInterval: live && !!id ? 5000 : false,
  });
  return (
    <>
      <Header
        title={live ? "Live calls" : "Call history"}
        description={
          live
            ? "Monitor actual call records. The browser never opens a provider media connection."
            : "Persisted call lifecycle, outcomes and order references. Raw audio is not recorded."
        }
        action={
          <Button onClick={() => query.refetch()}>Refresh records</Button>
        }
      />
      {tel.isError ? (
        <Failure error={tel.error} retry={() => tel.refetch()} />
      ) : (
        <Alert
          severity={tel.data?.simulated ? "warning" : "info"}
          sx={{ mb: 3 }}
        >
          {tel.data?.simulated
            ? "Mock / Simulation — these records are not evidence of live PSTN calls."
            : !tel.data?.configured || !tel.data?.enabled
              ? "Telephony is not configured. Live incoming calls are unavailable."
              : tel.data.live_verified
                ? "Backend reports live calling verified."
                : "Provider configuration exists; live telephone verification is pending."}
        </Alert>
      )}
      {live && (
        <Panel title="Place a live call">
          <Stack direction={{ xs: "column", sm: "row" }} spacing={2} alignItems="flex-start">
            <TextField
              label="Destination (E.164)"
              placeholder="+919876543210"
              value={destination}
              onChange={(e) => setDestination(e.target.value)}
              sx={{ minWidth: 240 }}
            />
            <TextField
              select
              label="Language"
              value={language}
              onChange={(e) => setLanguage(e.target.value as "hi" | "en")}
              sx={{ minWidth: 140 }}
            >
              <MenuItem value="hi">हिन्दी</MenuItem>
              <MenuItem value="en">English</MenuItem>
            </TextField>
            <Button
              variant="contained"
              disabled={!tel.data?.outbound_ready || !destination.trim() || dial.isPending}
              onClick={() => dial.mutate()}
            >
              {dial.isPending ? "Dialling…" : "Dial now"}
            </Button>
          </Stack>
          {!tel.data?.outbound_ready && (
            <Alert severity="warning" sx={{ mt: 2 }}>
              Outbound calling is not ready. Configure:{" "}
              {tel.data?.missing_configuration?.join(", ") || "telephony provider"}.
            </Alert>
          )}
          {dial.isError && <Failure error={dial.error} retry={() => dial.mutate()} />}
          {dial.isSuccess && (
            <Alert severity="success" sx={{ mt: 2 }}>
              Call requested: <code>{dial.data.call_id}</code>
            </Alert>
          )}
        </Panel>
      )}
      <Panel
        title={
          live ? `Active calls · ${query.data?.total ?? "—"}` : "Recorded calls"
        }
      >
        {!live && (
          <Box className="toolbar">
            <TextField
              select
              label="Call status"
              size="small"
              value={status}
              sx={{ minWidth: 180 }}
              onChange={(e) => {
                setStatus(e.target.value);
                setOffset(0);
              }}
            >
              {[
                "",
                "COMPLETED",
                "DISCONNECTED",
                "FAILED",
                "RINGING",
                "ACTIVE",
                "PROCESSING",
                "CONFIRMING",
              ].map((s) => (
                <MenuItem value={s} key={s}>
                  {s || "All statuses"}
                </MenuItem>
              ))}
            </TextField>
          </Box>
        )}
        {query.isPending ? (
          <Loading />
        ) : query.isError ? (
          <Failure error={query.error} retry={() => query.refetch()} />
        ) : (
          <>
            {!query.data.items.length ? (
              <Empty
                text={
                  live
                    ? "There are no active call records."
                    : "No provider or simulated calls have been recorded."
                }
              />
            ) : (
              <DataTable
                columns={[
                  "Call reference",
                  "Provider",
                  "Caller",
                  "Started",
                  "Duration",
                  "Status",
                  "Order",
                  "Details",
                ]}
                rows={query.data.items.map((c) => [
                  <code>{c.provider_call_reference}</code>,
                  c.provider === "mock" ? (
                    <Status value="MOCK / SIMULATION" />
                  ) : (
                    c.provider
                  ),
                  phone(c.customer_phone),
                  date(c.started_at),
                  elapsed(c.started_at, c.ended_at, now),
                  <Status value={c.status} />,
                  c.confirmed_order_id?.slice(-10) || "Not saved",
                  <Button onClick={() => setId(c.call_id)}>View call</Button>,
                ])}
              />
            )}
            <Pager
              offset={offset}
              total={query.data.total}
              onChange={setOffset}
            />
          </>
        )}
      </Panel>
      <Drawer anchor="right" open={!!id} onClose={() => setId(null)}>
        <Box className="detail-drawer">
          <Button onClick={() => setId(null)}>Close call</Button>
          {detail.isPending ? (
            <Loading />
          ) : detail.isError ? (
            <Failure error={detail.error} retry={() => detail.refetch()} />
          ) : (
            detail.data && (
              <Stack spacing={2} mt={3}>
                <Typography variant="h5">Call details</Typography>
                <Status value={detail.data.status} />
                {Object.entries(detail.data)
                  .filter(([key]) => key !== "transcripts")
                  .map(([key, value]) => (
                    <Box key={key}>
                      <Typography variant="overline" color="text.secondary">
                        {key.replaceAll("_", " ")}
                      </Typography>
                      <Typography className="reference">
                        {value === null ? "Not available" : String(value)}
                      </Typography>
                    </Box>
                  ))}
                <Typography>
                  Duration:{" "}
                  {elapsed(detail.data.started_at, detail.data.ended_at, now)}
                </Typography>
                <Divider />
                <Typography variant="h6">Conversation transcript</Typography>
                {detail.data.transcripts?.length ? (
                  detail.data.transcripts.map((turn) => (
                    <Box key={turn.sequence}>
                      <Typography variant="overline" color="text.secondary">
                        {turn.speaker}
                      </Typography>
                      <Typography>{turn.text}</Typography>
                    </Box>
                  ))
                ) : (
                  <Typography color="text.secondary">
                    No recognized turns recorded for this call.
                  </Typography>
                )}
              </Stack>
            )
          )}
        </Box>
      </Drawer>
    </>
  );
}
