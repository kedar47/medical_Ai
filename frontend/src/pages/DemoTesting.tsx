import { useState } from "react";
import {
  Alert,
  Box,
  Button,
  MenuItem,
  TextField,
  Typography,
} from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { Failure, Header, Panel } from "../components/common";
const scenarios = [
  "Single medicine order",
  "Multiple medicine order",
  "Hindi conversation",
  "English conversation",
  "Hinglish conversation",
  "Unclear pronunciation / clarification",
  "Unknown medicine rejection",
  "Quantity correction",
  "Confirmed order retrieval",
];
const results: Record<string, string> = {};
export default function DemoTesting() {
  const [checks, setChecks] = useState({ ...results });
  const [reference, setReference] = useState("");
  const cache = useQueryClient();
  const tel = useQuery({
    queryKey: ["telephony"],
    queryFn: ({ signal }) => api.telephony(signal),
  });
  const mock = useMutation({
    mutationFn: api.mock,
    onSuccess: () => {
      cache.invalidateQueries({ queryKey: ["calls"] });
      cache.invalidateQueries({ queryKey: ["stats"] });
    },
  });
  return (
    <>
      <Header
        title="Demo testing workspace"
        description="Operator observations for this browser visit. These are manual notes, not automated test evidence."
      />
      <Box className="two-col">
        <Panel title="Manual scenario checklist">
          <Alert severity="info" sx={{ mb: 2 }}>
            Results stay in memory for this browser visit and reset on reload.
            No transcripts are stored.
          </Alert>
          {scenarios.map((s) => (
            <Box className="check-row" key={s}>
              <Typography>{s}</Typography>
              <TextField
                select
                size="small"
                label={`Result: ${s}`}
                value={checks[s] || "Not Tested"}
                onChange={(e) => {
                  results[s] = e.target.value;
                  setChecks({ ...results });
                }}
              >
                {["Not Tested", "Passed", "Failed", "Blocked"].map((r) => (
                  <MenuItem key={r} value={r}>
                    {r}
                  </MenuItem>
                ))}
              </TextField>
            </Box>
          ))}
        </Panel>
        <Box>
          <Panel title="Telephone acceptance">
            <Alert severity={tel.data?.live_verified ? "success" : "warning"}>
              {tel.data?.live_verified
                ? "Verified — a real provider-connected call record exists."
                : "Blocked — no successful real two-way telephone call has been verified."}
            </Alert>
            <Typography color="text.secondary" mt={2}>
              Live Hindi, English, Hinglish, quantity correction and disconnect
              scenarios require an actual handset call from Live Calls. Mock
              results cannot satisfy them.
            </Typography>
            {!!tel.data?.missing_configuration?.length && (
              <Typography variant="body2" color="text.secondary" mt={1}>
                Missing: {tel.data.missing_configuration.join(", ")}
              </Typography>
            )}
          </Panel>
          <Panel title="Mock incoming event">
            <Alert severity="info" sx={{ mb: 2 }}>
              Simulation only. This creates a call mapping, not an audio
              conversation or real incoming phone call.
            </Alert>
            <TextField
              fullWidth
              label="Unique simulated call reference"
              value={reference}
              onChange={(e) => setReference(e.target.value)}
            />
            <Button
              sx={{ mt: 2 }}
              variant="outlined"
              disabled={
                !tel.data?.enabled ||
                !tel.data.simulated ||
                !reference.trim() ||
                mock.isPending
              }
              onClick={() => mock.mutate(reference.trim())}
            >
              Create simulated incoming event
            </Button>
            {(!tel.data?.enabled || !tel.data?.simulated) && (
              <Typography variant="body2" color="text.secondary" mt={2}>
                Requires explicitly enabled mock mode and a server-side monitor
                token.
              </Typography>
            )}
            {mock.isError && <Failure error={mock.error} />}{" "}
            {mock.data && (
              <Alert severity="success" sx={{ mt: 2 }}>
                SIMULATED: {mock.data.call_id} · {mock.data.status}
              </Alert>
            )}
          </Panel>
        </Box>
      </Box>
    </>
  );
}
