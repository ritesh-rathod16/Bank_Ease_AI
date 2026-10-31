import { z } from "zod";

export const loginSchema = z.object({ email: z.string().email(), password: z.string().min(1).max(128) });
export const registerSchema = z.object({
  name: z.string().trim().min(1, "Enter your name.").max(80),
  email: z.string().email(),
  password: z.string().min(12, "Use at least 12 characters.").max(128),
  password_confirmation: z.string(),
}).refine((value) => value.password === value.password_confirmation, { path: ["password_confirmation"], message: "Passwords do not match." });
export const emailSchema = z.object({ email: z.string().email() });
export const resetSchema = z.object({
  password: z.string().min(12, "Use at least 12 characters.").max(128),
  password_confirmation: z.string(),
}).refine((value) => value.password === value.password_confirmation, { path: ["password_confirmation"], message: "Passwords do not match." });
export const passwordSchema = z.object({
  current_password: z.string().min(1),
  new_password: z.string().min(12, "Use at least 12 characters.").max(128),
  password_confirmation: z.string(),
}).refine((value) => value.new_password === value.password_confirmation, { path: ["password_confirmation"], message: "Passwords do not match." });
