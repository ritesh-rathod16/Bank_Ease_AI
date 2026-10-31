import type { Metadata } from "next";
import { ReviewQueue } from "@/features/reviews/review-queue";

export const metadata: Metadata = { title: "Review queue" };

export default function ReviewsPage() {
  return <ReviewQueue />;
}
