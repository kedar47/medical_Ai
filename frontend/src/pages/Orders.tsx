import { useState } from "react";
import { Alert, Box, Button, Drawer, Typography } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
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
export default function Orders() {
  const [offset, setOffset] = useState(0);
  const [id, setId] = useState<string | null>(null);
  const query = useQuery({
    queryKey: ["orders", offset],
    queryFn: ({ signal }) => api.orders(offset, signal),
  });
  const detail = useQuery({
    queryKey: ["order", id],
    queryFn: ({ signal }) => api.order(id!, signal),
    enabled: !!id,
  });
  return (
    <>
      <Header
        title="Confirmed orders"
        description="Persisted customer approvals, ready for pharmacy review. Orders remain read-only."
      />
      <Panel>
        {query.isPending ? (
          <Loading />
        ) : query.isError ? (
          <Failure error={query.error} retry={() => query.refetch()} />
        ) : (
          <>
            {!query.data.items.length ? (
              <Empty text="Orders appear here only after successful explicit confirmation." />
            ) : (
              <DataTable
                columns={[
                  "Order reference",
                  "Customer",
                  "Created",
                  "Medicines",
                  "Status",
                  "Details",
                ]}
                rows={query.data.items.map((o) => [
                  <code>{o.order_id.slice(-12)}</code>,
                  o.customer_name || phone(o.customer_phone),
                  date(o.created_at),
                  o.total_line_items,
                  <Status value={o.status} />,
                  <Button onClick={() => setId(o.order_id)}>View order</Button>,
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
        <Box className="detail-drawer wide">
          <Button onClick={() => setId(null)}>Close order</Button>
          {detail.isPending ? (
            <Loading />
          ) : detail.isError ? (
            <Failure error={detail.error} retry={() => detail.refetch()} />
          ) : (
            detail.data && (
              <>
                <Typography variant="h5" mt={3}>
                  Order details
                </Typography>
                <Typography className="reference" my={2}>
                  {detail.data.order_id}
                </Typography>
                <Status value={detail.data.status} />
                <Typography my={2}>
                  {date(detail.data.created_at)} ·{" "}
                  {detail.data.customer_name ||
                    phone(detail.data.customer_phone)}
                </Typography>
                <DataTable
                  columns={["Medicine", "Strength / pack", "Quantity"]}
                  rows={detail.data.line_items.map((i) => [
                    i.medicine_name,
                    i.medicine_description,
                    `${i.quantity} ${i.unit}`,
                  ])}
                />
                <Typography variant="body2" mt={3}>
                  Call reference:{" "}
                  {detail.data.call_reference_id || "Browser / API order"}
                </Typography>
                <Alert severity="info" sx={{ mt: 3 }}>
                  This is an order for pharmacy review. No dispensing, payment
                  or dosage advice is provided.
                </Alert>
              </>
            )
          )}
        </Box>
      </Drawer>
    </>
  );
}
