import { useEffect, useState } from "react";
import { Box, Button, Drawer, TextField, Typography } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Medicine } from "../types";
import {
  DataTable,
  Empty,
  Failure,
  Header,
  Loading,
  Pager,
  Panel,
  Status,
} from "../components/common";
export default function Medicines() {
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<Medicine | null>(null);
  useEffect(() => {
    const t = setTimeout(() => {
      setQ(search);
      setOffset(0);
    }, 300);
    return () => clearTimeout(t);
  }, [search]);
  const query = useQuery({
    queryKey: ["medicines", q, offset],
    queryFn: ({ signal }) => api.medicines(q, offset, signal),
  });
  return (
    <>
      <Header
        title="Your approved catalogue"
        description="Read-only medicines from the original catalogue. Every strength and pack stays separate."
      />
      <Panel>
        <Box className="toolbar">
          <TextField
            label="Search medicines"
            size="small"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <Typography color="text.secondary" variant="body2">
            {query.data?.total ?? "—"} matching records · Read only
          </Typography>
        </Box>
        {query.isPending ? (
          <Loading />
        ) : query.isError ? (
          <Failure error={query.error} retry={() => query.refetch()} />
        ) : (
          <>
            {!query.data.items.length ? (
              <Empty text="No catalogue medicines match this search." />
            ) : (
              <DataTable
                columns={[
                  "Medicine ID",
                  "Name",
                  "Strength / packaging",
                  "Source row",
                  "Status",
                  "Details",
                ]}
                rows={query.data.items.map((m) => [
                  <code>{m.medicine_id}</code>,
                  <strong>{m.medicine_name}</strong>,
                  m.description,
                  m.source_row ?? "—",
                  <Status value={m.is_active ? "ACTIVE" : "INACTIVE"} />,
                  <Button
                    onClick={() => setSelected(m)}
                    aria-label={`View ${m.medicine_id}`}
                  >
                    View
                  </Button>,
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
      <Drawer
        anchor="right"
        open={!!selected}
        onClose={() => setSelected(null)}
      >
        <Box className="detail-drawer">
          <Button onClick={() => setSelected(null)}>Close details</Button>
          <Typography variant="overline" display="block" mt={3}>
            {selected?.medicine_id}
          </Typography>
          <Typography variant="h5">{selected?.medicine_name}</Typography>
          <Typography my={3}>{selected?.description}</Typography>
          <Typography>
            Original serial: {selected?.source_serial_number || "—"}
          </Typography>
          <Typography>Workbook row: {selected?.source_row || "—"}</Typography>
        </Box>
      </Drawer>
    </>
  );
}
