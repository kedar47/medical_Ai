import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Paper,
  Skeleton,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import type { ReactNode } from "react";
import { errorMessage } from "../api/client";
export const date = (value?: string | null) =>
  value
    ? new Date(value).toLocaleString("en-IN", {
        dateStyle: "medium",
        timeStyle: "short",
      })
    : "—";
export const phone = (value?: string | null) =>
  value ? `•••• ${value.slice(-4)}` : "Not provided";
export function Status({ value }: { value: string }) {
  const good = [
    "ACTIVE",
    "COMPLETED",
    "CONFIRMED",
    "Ready",
    "Connected",
  ].includes(value);
  const bad = ["FAILED", "Error", "Unavailable"].includes(value);
  return (
    <Chip
      size="small"
      label={value.replaceAll("_", " ")}
      color={good ? "success" : bad ? "error" : "default"}
      variant="outlined"
    />
  );
}
export function Header({
  eyebrow,
  title,
  description,
  action,
}: {
  eyebrow?: string;
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <Box className="page-header">
      <Box>
        <Typography className="eyebrow">{eyebrow || "WORKSPACE"}</Typography>
        <Typography variant="h4" component="h1">
          {title}
        </Typography>
        <Typography color="text.secondary" mt={1}>
          {description}
        </Typography>
      </Box>
      {action}
    </Box>
  );
}
export function Panel({
  title,
  children,
  action,
}: {
  title?: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <Paper className="panel" variant="outlined">
      {title && (
        <Box className="panel-heading">
          <Typography variant="h6">{title}</Typography>
          {action}
        </Box>
      )}
      {children}
    </Paper>
  );
}
export function Failure({
  error,
  retry,
}: {
  error: unknown;
  retry?: () => void;
}) {
  return (
    <Alert
      severity="error"
      action={
        retry ? (
          <Button color="inherit" onClick={retry}>
            Retry
          </Button>
        ) : undefined
      }
    >
      {errorMessage(error)}
    </Alert>
  );
}
export function Loading() {
  return (
    <Box aria-label="Loading data" role="status">
      <Skeleton height={65} />
      <Skeleton height={100} />
      <Skeleton height={100} />
    </Box>
  );
}
export function Empty({ text = "No records yet." }: { text?: string }) {
  return (
    <Box className="empty">
      <Typography variant="h6">Nothing here yet</Typography>
      <Typography color="text.secondary">{text}</Typography>
    </Box>
  );
}
export function Busy({ text = "Processing…" }: { text?: string }) {
  return (
    <Box role="status" className="busy">
      <CircularProgress size={18} />
      {text}
    </Box>
  );
}
export function DataTable({
  columns,
  rows,
}: {
  columns: string[];
  rows: ReactNode[][];
}) {
  return (
    <TableContainer>
      <Table size="small">
        <TableHead>
          <TableRow>
            {columns.map((c) => (
              <TableCell key={c}>{c}</TableCell>
            ))}
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((cells, i) => (
            <TableRow key={i}>
              {cells.map((c, j) => (
                <TableCell key={j}>{c}</TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </TableContainer>
  );
}
export function Pager({
  offset,
  total,
  onChange,
}: {
  offset: number;
  total: number;
  onChange: (n: number) => void;
}) {
  return (
    <Box className="pager">
      <Typography variant="body2" color="text.secondary">
        {total === 0
          ? "0 records"
          : `${offset + 1}–${Math.min(offset + 10, total)} of ${total}`}
      </Typography>
      <Button
        disabled={offset === 0}
        onClick={() => onChange(Math.max(0, offset - 10))}
      >
        Previous
      </Button>
      <Button
        disabled={offset + 10 >= total}
        onClick={() => onChange(offset + 10)}
      >
        Next
      </Button>
    </Box>
  );
}
export function ConfirmDialog({
  open,
  title,
  children,
  onClose,
  onConfirm,
  busy = false,
  disabled = false,
}: {
  open: boolean;
  title: string;
  children: ReactNode;
  onClose: () => void;
  onConfirm: () => void;
  busy?: boolean;
  disabled?: boolean;
}) {
  return (
    <Dialog
      open={open}
      onClose={busy ? undefined : onClose}
      aria-labelledby="confirm-title"
      fullWidth
      maxWidth="sm"
    >
      <DialogTitle id="confirm-title">{title}</DialogTitle>
      <DialogContent>{children}</DialogContent>
      <DialogActions>
        <Button disabled={busy} onClick={onClose}>
          Go back
        </Button>
        <Button
          variant="contained"
          disabled={busy || disabled}
          onClick={onConfirm}
        >
          {busy ? "Please wait…" : "Confirm"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
