import { Button, Stack, TextField } from "@mui/material";
import { useForm } from "react-hook-form";
import { z } from "zod";
export const customerSchema = z.object({
  customer_name: z.string().max(255),
  customer_phone: z
    .string()
    .refine(
      (v) => !v || /^\+[1-9]\d{7,14}$/.test(v),
      "Use E.164 format, for example +919876543210",
    ),
});
export function CustomerForm({
  onSubmit,
  busy,
  label = "Create session",
}: {
  onSubmit: (v: {
    customer_name: string | null;
    customer_phone: string | null;
  }) => void;
  busy: boolean;
  label?: string;
}) {
  const {
    register,
    handleSubmit,
    setError,
    formState: { errors },
  } = useForm<{ customer_name: string; customer_phone: string }>({
    defaultValues: { customer_name: "", customer_phone: "" },
  });
  return (
    <Stack
      component="form"
      spacing={2}
      onSubmit={handleSubmit((raw) => {
        const parsed = customerSchema.safeParse({
          customer_name: (raw.customer_name || "").trim(),
          customer_phone: (raw.customer_phone || "").trim(),
        });
        if (!parsed.success) {
          parsed.error.issues.forEach((i) =>
            setError(i.path[0] as "customer_phone", { message: i.message }),
          );
          return;
        }
        onSubmit({
          customer_name: parsed.data.customer_name || null,
          customer_phone: parsed.data.customer_phone || null,
        });
      })}
    >
      <TextField
        label="Customer name (optional)"
        {...register("customer_name")}
        error={!!errors.customer_name}
        helperText={errors.customer_name?.message}
      />
      <TextField
        label="Phone (optional, E.164)"
        {...register("customer_phone")}
        error={!!errors.customer_phone}
        helperText={
          errors.customer_phone?.message ||
          "No phone number is required for a browser demo."
        }
      />
      <Button type="submit" variant="contained" disabled={busy}>
        {label}
      </Button>
    </Stack>
  );
}
