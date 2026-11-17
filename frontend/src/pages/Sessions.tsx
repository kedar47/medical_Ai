import { useState } from "react";
import {
  Box,
  Button,
  Dialog,
  DialogContent,
  DialogTitle,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import { useApp } from "../components/AppContext";
import { CustomerForm } from "../components/CustomerForm";
import DraftEditor from "../components/DraftEditor";
import {
  ConfirmDialog,
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
export default function Sessions() {
  const [offset, setOffset] = useState(0);
  const [status, setStatus] = useState("");
  const [create, setCreate] = useState(false);
  const [cancel, setCancel] = useState<{ id: string; voice?: string } | null>(
    null,
  );
  const { active, setActive, notify } = useApp();
  const cache = useQueryClient();
  const query = useQuery({
    queryKey: ["sessions", offset, status],
    queryFn: ({ signal }) => api.sessions(offset, status, signal),
  });
  const mutation = useMutation({
    mutationFn: api.createSession,
    onSuccess: (s) => {
      setActive({ sessionId: s.session_id });
      setCreate(false);
      cache.invalidateQueries({ queryKey: ["sessions"] });
      cache.invalidateQueries({ queryKey: ["stats"] });
      notify("Fresh order session created.");
    },
    onError: (e) => notify(errorMessage(e), "error"),
  });
  const end = useMutation({
    mutationFn: async () => {
      if (cancel?.voice) await api.endVoice(cancel.voice);
      else if (cancel) await api.cancelSession(cancel.id);
    },
    onSuccess: () => {
      setCancel(null);
      cache.invalidateQueries({ queryKey: ["sessions"] });
      cache.invalidateQueries({ queryKey: ["session"] });
      cache.invalidateQueries({ queryKey: ["stats"] });
      notify("Session cancelled. No order was created.");
    },
    onError: (e) => notify(errorMessage(e), "error"),
  });
  return (
    <>
      <Header
        title="Order sessions"
        description="Create drafts, choose exact medicines and review the complete order before confirmation."
        action={
          <Button variant="contained" onClick={() => setCreate(true)}>
            New order session
          </Button>
        }
      />
      <Box className="session-grid">
        <Panel title="Session history">
          <Box className="toolbar">
            <TextField
              select
              size="small"
              label="Filter status"
              value={status}
              onChange={(e) => {
                setStatus(e.target.value);
                setOffset(0);
              }}
              sx={{ minWidth: 180 }}
            >
              {["", "ACTIVE", "CONFIRMED", "CANCELLED", "EXPIRED"].map((s) => (
                <MenuItem key={s} value={s}>
                  {s || "All sessions"}
                </MenuItem>
              ))}
            </TextField>
          </Box>
          {query.isPending ? (
            <Loading />
          ) : query.isError ? (
            <Failure error={query.error} retry={() => query.refetch()} />
          ) : (
            <>
              {!query.data.items.length ? (
                <Empty text="Create your first order session." />
              ) : (
                <DataTable
                  columns={[
                    "Session",
                    "Customer",
                    "Status",
                    "Items",
                    "Actions",
                  ]}
                  rows={query.data.items.map((s) => [
                    <>
                      <code>{s.session_id.slice(-10)}</code>
                      <Typography variant="caption" display="block">
                        {date(s.created_at)}
                      </Typography>
                    </>,
                    s.customer_name || phone(s.customer_phone),
                    <Status value={s.status} />,
                    s.item_count,
                    <Box className="inline">
                      <Button
                        onClick={() =>
                          setActive({
                            sessionId: s.session_id,
                            voiceId: s.voice_session_id || undefined,
                          })
                        }
                      >
                        Open
                      </Button>
                      <Button
                        color="error"
                        disabled={s.status !== "ACTIVE" || end.isPending}
                        onClick={() =>
                          setCancel({
                            id: s.session_id,
                            voice: s.voice_session_id || undefined,
                          })
                        }
                      >
                        Cancel
                      </Button>
                    </Box>,
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
        <Panel
          title="Selected draft"
          action={
            active ? (
              <Button component={Link} to="/matching">
                Add medicine
              </Button>
            ) : undefined
          }
        >
          {active ? (
            <DraftEditor
              sessionId={active.sessionId}
              voiceId={active.voiceId}
            />
          ) : (
            <Empty text="Open a session to inspect its items, status and confirmation workflow." />
          )}
        </Panel>
      </Box>
      <Dialog
        open={create}
        onClose={() => !mutation.isPending && setCreate(false)}
        fullWidth
        maxWidth="sm"
      >
        <DialogTitle>Create a fresh order session</DialogTitle>
        <DialogContent>
          <Box pt={1}>
            <CustomerForm
              busy={mutation.isPending}
              onSubmit={(v) => mutation.mutate(v)}
            />
            {mutation.isError && <Failure error={mutation.error} />}
          </Box>
        </DialogContent>
      </Dialog>
      <ConfirmDialog
        open={!!cancel}
        title="Cancel this order session?"
        onClose={() => setCancel(null)}
        onConfirm={() => end.mutate()}
        busy={end.isPending}
      >
        Its unconfirmed draft will be cancelled. A persisted confirmed order
        cannot be cancelled here.
      </ConfirmDialog>
    </>
  );
}
