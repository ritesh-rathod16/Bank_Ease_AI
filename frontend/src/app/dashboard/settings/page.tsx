import type { Metadata } from "next";
import { AccountSettings } from "@/features/settings/account-settings";
export const metadata: Metadata = { title: "Account & security" };
export default function SettingsPage() { return <AccountSettings />; }
