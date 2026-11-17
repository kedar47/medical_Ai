import { useEffect, useState } from "react";
import {
  Alert,
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
import type { Item, Summary, Unit } from "../types";
import { units } from "../types";
import {
  ConfirmDialog,
  DataTable,
  Empty,
  Failure,
  Loading,
  Status,
  date,
} from "./common";
import { useApp } from "./AppContext";

export default function DraftEditor({
  sessionId,
  voiceId,
}: {
  sessionId: string;
  voiceId?: string;
}) {
  const cache = useQueryClient();
  const { notify } = useApp();
  const [edit, setEdit] = useState<Item | null>(null);
  const [quantity, setQuantity] = useState(1);
  const [unit, setUnit] = useState<Unit>("STRIP");
  const [remove, setRemove] = useState<Item | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [confirmed, setConfirmed] = useState<string | null>(null);
  const [accepted, setAccepted] = useState(false);
  const query = useQuery({
    queryKey: ["session", sessionId],
    queryFn: ({ signal }) => api.session(sessionId, signal),
  });
  const refresh = () => {
    cache.invalidateQueries({ queryKey: ["session", sessionId] });
    cache.invalidateQueries({ queryKey: ["sessions"] });
    cache.invalidateQueries({ queryKey: ["stats"] });
    cache.invalidateQueries({ queryKey: ["orders"] });
  };
  const mutation = useMutation({
    mutationFn: async (action: "update" | "remove" | "summary" | "confirm") => {
      if (action === "summary")
        return { summary: await api.summary(sessionId) };
      if (action === "confirm") {
        if (!summary?.confirmation_token || !accepted)
          throw new Error("Review a fresh summary and explicitly approve it.");
        return {
          order: await api.confirm(sessionId, summary.confirmation_token),
        };
      }
      setSummary(null);
      setAccepted(false);
      if (action === "update" && edit) {
        if (!Number.isInteger(quantity) || quantity <= 0)
          throw new Error("Quantity must be a positive whole number.");
        if (voiceId) {
          const result = await api.turn(
            voiceId,
            `change ${edit.medicine_id} quantity to ${quantity} ${unit.toLowerCase()}`,
            false,
          );
          if (!result.order_summary) throw new Error(result.assistant_text);
        } else await api.updateItem(sessionId, edit.id, quantity, unit);
      }
      if (action === "remove" && remove) {
        if (voiceId) {
          const result = await api.turn(
            voiceId,
            `remove ${remove.medicine_id}`,
            false,
          );
          if (!result.order_summary) throw new Error(result.assistant_text);
        } else await api.removeItem(sessionId, remove.id);
      }
      return {};
    },
    onSuccess: (result) => {
      if ("summary" in result) setSummary(result.summary!);
      else {
        setEdit(null);
        setRemove(null);
      }
      if ("order" in result) {
        setConfirmed(result.order!.order_id);
        setSummary(null);
        notify("Order saved for pharmacy review.");
      }
      refresh();
    },
    onError: (e) => {
      setSummary(null);
      setAccepted(false);
      notify(errorMessage(e), "error");
      refresh();
    },
  });
  useEffect(() => {
    setSummary(null);
    setConfirmed(null);
    setAccepted(false);
  }, [sessionId]);
  if (query.isPending) return <Loading />;
  if (query.isError)
    return <Failure error={query.error} retry={() => query.refetch()} />;
  const session = query.data;
  const active =
    session.status === "ACTIVE" && new Date(session.expires_at) > new Date();
  return (
    <Stack spacing={2}>
      <Box className="inline">
        <Typography variant="overline">
          Draft · {sessionId.slice(-8)}
        </Typography>
        <Status value={session.status} />
      </Box>
      <Typography variant="caption" color="text.secondary">
        Created {date(session.created_at)} · Expires {date(session.expires_at)}
      </Typography>
      {confirmed && (
        <Alert severity="success">
          Order persisted: <Link to="/orders">{confirmed}</Link>
        </Alert>
      )}
      {mutation.isError && <Failure error={mutation.error} />}
      {!session.items.length ? (
        <Empty text="Choose an exact catalogue medicine to begin." />
      ) : (
        <DataTable
          columns={["Medicine", "Quantity", "Actions"]}
          rows={session.items.map((item) => [
            <>
              <strong>{item.medicine_name}</strong>
              <Typography variant="caption" display="block">
                {item.description}
              </Typography>
            </>,
            `${item.quantity} ${item.unit}`,
            <Box className="inline">
              <Button
                size="small"
                disabled={!active || mutation.isPending}
                onClick={() => {
                  setSummary(null);
                  setEdit(item);
                  setQuantity(item.quantity);
                  setUnit(item.unit);
                }}
                aria-label={`Edit ${item.medicine_name}`}
              >
                Edit
              </Button>
              <Button
                size="small"
                color="error"
                disabled={!active || mutation.isPending}
                onClick={() => setRemove(item)}
                aria-label={`Remove ${item.medicine_name}`}
              >
                Remove
              </Button>
            </Box>,
          ])}
        />
      )}
      {voiceId ? (
        <Alert severity="info">
          Review and confirm this voice order in the{" "}
          <Link to="/voice">AI Voice Assistant</Link>. Its correction guard
          remains authoritative.
        </Alert>
      ) : (
        <Button
          variant="contained"
          disabled={!active || !session.items.length || mutation.isPending}
          onClick={() => {
            setAccepted(false);
            mutation.mutate("summary");
          }}
        >
          Review complete order
        </Button>
      )}
      <Dialog
        open={!!edit}
        onClose={() => !mutation.isPending && setEdit(null)}
        fullWidth
        maxWidth="xs"
      >
        <DialogTitle>Change quantity</DialogTitle>
        <DialogContent>
          <Stack spacing={2} pt={1}>
            <TextField
              label="New quantity"
              type="number"
              value={quantity}
              onChange={(e) => setQuantity(Number(e.target.value))}
              inputProps={{ min: 1, step: 1 }}
            />
            <TextField
              select
              label="Unit"
              value={unit}
              onChange={(e) => setUnit(e.target.value as Unit)}
            >
              {units.map((u) => (
                <MenuItem key={u} value={u}>
                  {u}
                </MenuItem>
              ))}
            </TextField>
            <Button
              variant="contained"
              disabled={mutation.isPending}
              onClick={() => mutation.mutate("update")}
            >
              Save change
            </Button>
          </Stack>
        </DialogContent>
      </Dialog>
      <ConfirmDialog
        open={!!remove}
        title="Remove this medicine?"
        onClose={() => setRemove(null)}
        onConfirm={() => mutation.mutate("remove")}
        busy={mutation.isPending}
      >
        {remove?.medicine_name} will be removed from this draft.
      </ConfirmDialog>
      <ConfirmDialog
        open={!!summary}
        title="Review every medicine"
        onClose={() => setSummary(null)}
        onConfirm={() => mutation.mutate("confirm")}
        busy={mutation.isPending}
        disabled={!accepted || !summary?.can_confirm}
      >
        {summary?.items.map((i) => (
          <Typography key={i.id} mb={1}>
            {i.medicine_name} · {i.description} — {i.quantity} {i.unit}
          </Typography>
        ))}
        <Alert severity="info">{summary?.message}</Alert>
        <label className="consent">
          <input
            type="checkbox"
            checked={accepted}
            onChange={(e) => setAccepted(e.target.checked)}
          />
          I explicitly confirm this complete medicine order.
        </label>
      </ConfirmDialog>
    </Stack>
  );
}
