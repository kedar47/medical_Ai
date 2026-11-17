import { Alert, Box, Button, Typography } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { Failure, Header, Loading, Panel, Status } from "../components/common";
export default function SystemStatus() {
  const health = useQuery({
    queryKey: ["health"],
    queryFn: ({ signal }) => api.health(signal),
  });
  const voice = useQuery({
    queryKey: ["voice-status"],
    queryFn: ({ signal }) => api.voiceStatus(signal),
  });
  const tel = useQuery({
    queryKey: ["telephony"],
    queryFn: ({ signal }) => api.telephony(signal),
  });
  return (
    <>
      <Header
        title="System status"
        description="Configuration and health from actual backend responses. Configured does not mean live-call verified."
        action={
          <Button
            onClick={() => {
              health.refetch();
              voice.refetch();
              tel.refetch();
            }}
          >
            Check again
          </Button>
        }
      />
      <Box className="two-col">
        <Panel title="Backend & database">
          {health.isPending ? (
            <Loading />
          ) : health.isError ? (
            <Failure error={health.error} retry={() => health.refetch()} />
          ) : (
            <Box className="status-list">
              <Box>
                FastAPI{" "}
                <Status
                  value={health.data.status === "ok" ? "Ready" : "Unavailable"}
                />
              </Box>
              <Box>
                PostgreSQL{" "}
                <Status
                  value={
                    health.data.database === "connected"
                      ? "Connected"
                      : "Unavailable"
                  }
                />
              </Box>
            </Box>
          )}
        </Panel>
        <Panel title="Local voice services">
          {voice.isPending ? (
            <Loading />
          ) : voice.isError ? (
            <Failure error={voice.error} retry={() => voice.refetch()} />
          ) : (
            <>
              <Typography mb={2}>
                Voice API: {voice.data.enabled ? "Enabled" : "Disabled"}
              </Typography>
              {(["stt", "tts", "llm"] as const).map((key) => (
                <Box className="service-row" key={key}>
                  <Box>
                    <strong>{voice.data[key].provider}</strong>
                    <Typography variant="body2" color="text.secondary">
                      {voice.data[key].detail}
                    </Typography>
                  </Box>
                  <Status
                    value={
                      key === "llm" &&
                      voice.data[key].detail.includes("disabled")
                        ? "Disabled"
                        : voice.data[key].ready
                          ? "Configured"
                          : "Unavailable"
                    }
                  />
                </Box>
              ))}
            </>
          )}
        </Panel>
        <Panel title="Telephone integration">
          {tel.isPending ? (
            <Loading />
          ) : tel.isError ? (
            <Failure error={tel.error} retry={() => tel.refetch()} />
          ) : (
            <Box className="status-list">
              <Box>
                Provider <strong>{tel.data.provider}</strong>
              </Box>
              <Box>
                Transport{" "}
                <Status value={tel.data.enabled ? "Enabled" : "Disabled"} />
              </Box>
              <Box>
                Credentials{" "}
                <Status
                  value={tel.data.configured ? "Configured" : "Not configured"}
                />
              </Box>
              <Box>
                Mode{" "}
                <Status
                  value={
                    tel.data.simulated
                      ? "MOCK / SIMULATION"
                      : "Provider adapter"
                  }
                />
              </Box>
              <Box>
                Inbound stream{" "}
                <Status
                  value={tel.data.inbound_ready ? "Ready" : "Not ready"}
                />
              </Box>
              <Box>
                Outbound dialling{" "}
                <Status
                  value={tel.data.outbound_ready ? "Ready" : "Not ready"}
                />
              </Box>
              <Box>
                Live calling{" "}
                <Status
                  value={
                    tel.data.live_verified ? "Verified" : "Verification pending"
                  }
                />
              </Box>
              {!!tel.data.missing_configuration?.length && (
                <Typography variant="body2" color="text.secondary">
                  Missing: {tel.data.missing_configuration.join(", ")}
                </Typography>
              )}
              <Typography variant="body2" color="text.secondary">
                A real two-way telephone call is required before this changes to
                Verified.
              </Typography>
            </Box>
          )}
        </Panel>
        <Panel title="Operating notes">
          <Alert severity="warning">
            Local STT/TTS was previously measured at 27–41 seconds. A responsive
            interface does not reduce inference time.
          </Alert>
          <Typography mt={2} color="text.secondary">
            Orders require explicit confirmation and remain pending pharmacy
            review. Provider setup, a verified number and handset tests are
            still required.
          </Typography>
        </Panel>
      </Box>
    </>
  );
}
