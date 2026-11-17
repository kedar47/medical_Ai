import { useState } from "react";
import {
  Alert,
  Box,
  Button,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import { units, type Unit, type Candidate } from "../types";
import { useApp } from "../components/AppContext";
import {
  Busy,
  ConfirmDialog,
  Failure,
  Header,
  Panel,
  Status,
} from "../components/common";
export default function Matching() {
  const [text, setText] = useState("");
  const [selected, setSelected] = useState<Candidate | null>(null);
  const [quantity, setQuantity] = useState(1);
  const [unit, setUnit] = useState<Unit>("STRIP");
  const { active, notify } = useApp();
  const cache = useQueryClient();
  const match = useMutation({ mutationFn: api.match });
  const add = useMutation({
    mutationFn: async () => {
      if (!active || !selected)
        throw new Error("Start or select an active order session first.");
      if (!Number.isInteger(quantity) || quantity < 1)
        throw new Error("Enter a positive whole-number quantity.");
      if (active.voiceId) {
        const turn = await api.turn(
          active.voiceId,
          `add ${quantity} ${unit.toLowerCase()} ${selected.medicine_id}`,
          false,
        );
        if (
          !turn.order_summary?.items.some(
            (i) => i.medicine_id === selected.medicine_id,
          )
        )
          throw new Error(turn.assistant_text);
      } else
        await api.addItem(
          active.sessionId,
          selected.medicine_id,
          quantity,
          unit,
        );
    },
    onSuccess: () => {
      setSelected(null);
      notify("Medicine added to the active draft.");
      cache.invalidateQueries({ queryKey: ["session"] });
      cache.invalidateQueries({ queryKey: ["sessions"] });
    },
    onError: (e) => notify(errorMessage(e), "error"),
  });
  return (
    <>
      <Header
        title="Find the exact medicine"
        description="Deterministic matches, explicit variants. Suggestions are never added automatically."
      />
      <Box className="two-col">
        <Panel title="Medicine matching">
          <Stack
            component="form"
            spacing={2}
            onSubmit={(e) => {
              e.preventDefault();
              if (text.trim()) match.mutate(text.trim());
            }}
          >
            <TextField
              label="Medicine name or alias"
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="Try BRUFEN"
            />
            <Button
              type="submit"
              variant="contained"
              disabled={match.isPending || !text.trim()}
            >
              Find medicine
            </Button>
          </Stack>
          <Typography mt={3} color="text.secondary" variant="body2">
            Try the full medicine name, or a supported Hindi / Hinglish alias.
          </Typography>
        </Panel>
        <Panel title="Matching result">
          {match.isPending ? (
            <Busy text="Searching approved catalogue…" />
          ) : match.isError ? (
            <Failure error={match.error} />
          ) : match.data ? (
            <Stack spacing={2}>
              <Status value={match.data.status} />
              <Typography>{match.data.message}</Typography>
              {match.data.candidates.map((c) => (
                <Box className="candidate" key={c.medicine_id}>
                  <Box>
                    <strong>{c.medicine_name}</strong>
                    <Typography variant="body2">{c.description}</Typography>
                    <Typography variant="caption">
                      {c.medicine_id} · {c.match_type}
                    </Typography>
                  </Box>
                  <Button
                    disabled={!active}
                    onClick={() => setSelected(c)}
                    aria-label={`Choose ${c.medicine_id}`}
                  >
                    Choose explicitly
                  </Button>
                </Box>
              ))}
              {!active && (
                <Alert severity="info">
                  Start a session in <Link to="/sessions">Order Sessions</Link>{" "}
                  before choosing a candidate.
                </Alert>
              )}
            </Stack>
          ) : (
            <Typography color="text.secondary">
              Search to inspect catalogue matches and variants.
            </Typography>
          )}
        </Panel>
      </Box>
      <ConfirmDialog
        open={!!selected}
        title="Add this exact catalogue variant?"
        onClose={() => setSelected(null)}
        onConfirm={() => add.mutate()}
        busy={add.isPending}
      >
        <Stack spacing={2}>
          <Typography>
            {selected?.medicine_name} · {selected?.description} ·{" "}
            {selected?.medicine_id}
          </Typography>
          {selected?.match_type === "FUZZY_SUGGESTION" && (
            <Alert severity="warning">
              This is an unverified fuzzy suggestion. Only choose it if you have
              identified this exact medicine.
            </Alert>
          )}
          <TextField
            label="Quantity"
            type="number"
            value={quantity}
            onChange={(e) => setQuantity(Number(e.target.value))}
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
          {add.isError && <Failure error={add.error} />}
        </Stack>
      </ConfirmDialog>
    </>
  );
}
