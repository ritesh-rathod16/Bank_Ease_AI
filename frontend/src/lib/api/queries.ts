import { apiFetch, apiStream, jsonRequest } from "@/lib/api/client";
import { normalizeConversationTitle } from "@/lib/conversation-title";
import type { AiStatus, AskResult, CompareRow, ComparisonHistoryItem, ConversationMessage, ConversationMessages, ConversationSummary, DashboardSummary, DocumentProcessingStatus, DocumentRecord, DocumentSummary, DocumentVersionHistory, FinanceSummary, FinancialStatementComparisonResult, ReviewTask, SavedComparison, SearchResult, Session, StatementComparisonResult, StatementTransactionPage, TransactionCorrectionRequest, StatementTransaction, User, ContractAnalysis, ContractClause, ContractRisk, ContractComplianceCheck, ContractObligation, ContractOverview, ContractFinancialTerm, ContractSummary, ContractCompareResult, ContractRiskSettings } from "@/types/api";

export const api = {
  me: async () => (await apiFetch<{ user: User }>("/api/v1/auth/me")).user,
  documents: async () => (await apiFetch<{ documents: DocumentSummary[] }>("/api/v1/documents")).documents,
  dashboard: () => apiFetch<DashboardSummary>("/api/v1/dashboard/summary"),
  search: (q: string) => apiFetch<{ query: string; results: SearchResult[] }>(`/api/v1/search?q=${encodeURIComponent(q)}&limit=20`),
  document: (id: string) => apiFetch<DocumentRecord>(`/api/v1/documents/${encodeURIComponent(id)}`),
  documentStatus: (id: string) => apiFetch<DocumentProcessingStatus>(`/api/v1/documents/${encodeURIComponent(id)}/status`),
  retryDocument: (id: string) => apiFetch<DocumentProcessingStatus>(`/api/v1/documents/${encodeURIComponent(id)}/retry`, jsonRequest("POST", {})),
  versions: (id: string) => apiFetch<DocumentVersionHistory>(`/api/v1/documents/${encodeURIComponent(id)}/versions`),
  terms: (id: string) => apiFetch<Record<string, DocumentRecord["terms"][string]>>(`/api/v1/documents/${encodeURIComponent(id)}/terms`),
  correctField: (id: string, field: string, value: string | null, reason = "") => apiFetch<DocumentRecord["terms"][string]>(`/api/v1/documents/${encodeURIComponent(id)}/fields/${encodeURIComponent(field)}`, jsonRequest("PATCH", { value, reason })),
  clauses: (id: string) => apiFetch<DocumentRecord["clauses"]>(`/api/v1/documents/${encodeURIComponent(id)}/clauses`),
  transactions: (id: string, offset: number, limit: number, filters: { search?: string; date_from?: string; date_to?: string } = {}) => {
    const query = new URLSearchParams({ offset: String(offset), limit: String(limit) });
    if (filters.search) query.set("search", filters.search);
    if (filters.date_from) query.set("date_from", filters.date_from);
    if (filters.date_to) query.set("date_to", filters.date_to);
    return apiFetch<StatementTransactionPage>(`/api/v1/documents/${encodeURIComponent(id)}/transactions?${query.toString()}`);
  },
  correctTransaction: (id: string, rowNumber: number, body: TransactionCorrectionRequest) => apiFetch<StatementTransaction>(`/api/v1/documents/${encodeURIComponent(id)}/transactions/${rowNumber}`, jsonRequest("PATCH", body)),
  upload: async (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return apiFetch<DocumentSummary>("/api/v1/documents", { method: "POST", body: form });
  },
  uploadRevision: async (file: File, supersedesDocumentId: string) => {
    const form = new FormData();
    form.append("file", file);
    form.append("supersedes_document_id", supersedesDocumentId);
    return apiFetch<DocumentSummary>("/api/v1/documents", { method: "POST", body: form });
  },
  renameDocument: (id: string, filename: string) => apiFetch<DocumentSummary>(`/api/v1/documents/${encodeURIComponent(id)}`, jsonRequest("PATCH", { filename })),
  removeDocument: (id: string) => apiFetch<void>(`/api/v1/documents/${encodeURIComponent(id)}`, { method: "DELETE" }),
  ask: (id: string, question: string) => apiFetch<AskResult>(`/api/v1/documents/${encodeURIComponent(id)}/ask`, jsonRequest("POST", { question })),
  conversations: () => apiFetch<{ conversations: ConversationSummary[] }>("/api/v1/conversations?limit=100"),
  updateConversation: (id: string, change: { title?: string; is_pinned?: boolean; document_ids?: string[] }) => apiFetch<ConversationSummary>(`/api/v1/conversations/${encodeURIComponent(id)}`, jsonRequest("PATCH", change)),
  aiStatus: () => apiFetch<AiStatus>("/api/v1/ai/status"),
  conversationMessages: (id: string) => apiFetch<ConversationMessages>(`/api/v1/conversations/${encodeURIComponent(id)}/messages`),
  createConversation: (document_ids: string[], title: string, mode = "local") => apiFetch<ConversationSummary>("/api/v1/conversations", jsonRequest("POST", { document_ids, title: normalizeConversationTitle(title), mode })),
  sendConversationMessageStream: (id: string, question: string, requestId: string, options: { mode: string; model?: string; allow_hosted: boolean; allow_hosted_documents: boolean; web_research: boolean }, onDelta: (text: string) => void, signal?: AbortSignal) => apiStream<{ conversation_id: string; answer: AskResult; messages: ConversationMessage[] }>(`/api/v1/conversations/${encodeURIComponent(id)}/messages/stream`, { question, request_id: requestId, ...options }, onDelta, signal),
  deleteConversation: (id: string) => apiFetch<void>(`/api/v1/conversations/${encodeURIComponent(id)}`, { method: "DELETE" }),
  calculate: (id: string, values?: { principal?: number; annual_rate?: number; months?: number }) => apiFetch<FinanceSummary>(`/api/v1/documents/${encodeURIComponent(id)}/calculate-emi`, jsonRequest("POST", values)),
  contractOverview: () => apiFetch<ContractOverview>("/api/v1/contracts/overview"),
  contracts: () => apiFetch<{ contracts: ContractSummary[] }>("/api/v1/contracts"),
  contractAnalysis: (id: string) => apiFetch<ContractAnalysis>(`/api/v1/contracts/${encodeURIComponent(id)}/analysis`),
  analyzeContract: (id: string, force = false) => apiFetch<{ status: string; progress: number; document_id: string; already_analyzed?: boolean }>(`/api/v1/contracts/${encodeURIComponent(id)}/analysis${force ? "?force=true" : ""}`, { method: "POST" }),
  contractClauses: (documentId?: string) => apiFetch<{ clauses: ContractClause[]; contracts_analyzed: number }>(`/api/v1/contracts/clauses${documentId ? `?document_id=${encodeURIComponent(documentId)}` : ""}`),
  contractRisks: (documentId?: string) => apiFetch<{ risks: ContractRisk[] }>(`/api/v1/contracts/risks${documentId ? `?document_id=${encodeURIComponent(documentId)}` : ""}`),
  contractCompliance: (documentId?: string) => apiFetch<{ checks: ContractComplianceCheck[]; note: string }>(`/api/v1/contracts/compliance${documentId ? `?document_id=${encodeURIComponent(documentId)}` : ""}`),
  contractObligations: (documentId?: string) => apiFetch<{ contractual_obligations: ContractObligation[]; user_reminders: ContractObligation[] }>(`/api/v1/contracts/obligations${documentId ? `?document_id=${encodeURIComponent(documentId)}` : ""}`),
  contractFinancial: (documentId?: string) => apiFetch<{ financial_analysis: Array<{ document_id: string; filename: string; terms: ContractFinancialTerm[]; annualized_recurring_total: string | null; note: string }> }>(`/api/v1/contracts/financial${documentId ? `?document_id=${encodeURIComponent(documentId)}` : ""}`),
  contractReports: () => apiFetch<{ reports: Array<{ document_id: string; filename: string; status: string; analyzed_at?: string; available_reports: string[] }> }>("/api/v1/contracts/reports"),
  compareContracts: (document_ids: string[]) => apiFetch<ContractCompareResult>("/api/v1/contracts/compare", jsonRequest("POST", { document_ids })),
  compareContractsReport: (document_ids: string[]) => apiFetch<Blob>("/api/v1/contracts/compare/report", jsonRequest("POST", { document_ids })),
  contractSettings: () => apiFetch<ContractRiskSettings>("/api/v1/contracts/settings"),
  saveContractSettings: (settings: ContractRiskSettings) => apiFetch<ContractRiskSettings>("/api/v1/contracts/settings", jsonRequest("PUT", settings)),
  createContractReminder: (documentId: string, input: { title: string; due_date: string; notes: string }) => apiFetch<ContractObligation>(`/api/v1/contracts/${encodeURIComponent(documentId)}/reminders`, jsonRequest("POST", input)),
  updateContractReminder: (documentId: string, reminderId: string, status: "open" | "completed") => apiFetch<ContractObligation>(`/api/v1/contracts/${encodeURIComponent(documentId)}/reminders/${encodeURIComponent(reminderId)}`, jsonRequest("PATCH", { status })),
  contractReport: (id: string, kind: "executive" | "detailed" | "risks" | "clauses" | "compliance") => apiFetch<Blob>(`/api/v1/contracts/${encodeURIComponent(id)}/report?kind=${kind}`),
  compare: (a: string, b: string, overrides_a?: Record<string, string>, overrides_b?: Record<string, string>) => apiFetch<{ a: string; b: string; rows: CompareRow[]; comparison_id: string; created_at: string }>("/documents/compare", jsonRequest("POST", { document_id_a: a, document_id_b: b, overrides_a, overrides_b })),
  compareReport: (a: string, b: string, overrides_a?: Record<string, string>, overrides_b?: Record<string, string>) => apiFetch<Blob>("/documents/compare/report", jsonRequest("POST", { document_id_a: a, document_id_b: b, overrides_a, overrides_b })),
  compareStatements: (a: string, b: string) => apiFetch<StatementComparisonResult>("/api/v1/comparisons/statements", jsonRequest("POST", { document_id_a: a, document_id_b: b })),
  compareFinancialStatements: (a: string, b: string) => apiFetch<FinancialStatementComparisonResult>("/api/v1/comparisons/financial-statements", jsonRequest("POST", { document_id_a: a, document_id_b: b })),
  compareStatementsReport: (a: string, b: string) => apiFetch<Blob>("/api/v1/comparisons/statements/report", jsonRequest("POST", { document_id_a: a, document_id_b: b })),
  comparisons: (kind: "loan" | "statement" | "financial_statement", offset = 0) => apiFetch<{ comparisons: ComparisonHistoryItem[]; offset: number; limit: number; has_more: boolean }>(`/api/v1/comparisons?kind=${kind}&offset=${offset}&limit=20`),
  comparison: (id: string) => apiFetch<SavedComparison>(`/api/v1/comparisons/${encodeURIComponent(id)}`),
  comparisonReport: (id: string) => apiFetch<Blob>(`/api/v1/comparisons/${encodeURIComponent(id)}/report`),
  removeComparison: (id: string) => apiFetch<void>(`/api/v1/comparisons/${encodeURIComponent(id)}`, { method: "DELETE" }),
  reviews: (documentId?: string) => apiFetch<{ reviews: ReviewTask[]; total: number; offset: number; limit: number }>(`/api/v1/reviews${documentId ? `?document_id=${encodeURIComponent(documentId)}` : ""}`),
  requestReview: (document_id: string, field_id: string, reason = "Please verify this extracted value against its cited source.") => apiFetch<ReviewTask & { created: boolean }>("/api/v1/reviews", jsonRequest("POST", { document_id, field_id, reason })),
  assignReview: (id: string, reviewer_email: string) => apiFetch<{ id: string; status: string; assigned_to: string }>(`/api/v1/reviews/${encodeURIComponent(id)}/assign`, jsonRequest("POST", { reviewer_email })),
  decideReview: (id: string, decision: "approved" | "needs_owner_action" | "rejected", comment = "") => apiFetch<{ id: string; status: string }>(`/api/v1/reviews/${encodeURIComponent(id)}/decision`, jsonRequest("POST", { decision, comment })),
  respondReview: (id: string, message: string) => apiFetch<{ id: string; status: string }>(`/api/v1/reviews/${encodeURIComponent(id)}/response`, jsonRequest("POST", { message })),
  staffUsers: () => apiFetch<{ users: User[] }>("/api/v1/admin/users"),
  changeRole: (email: string, role: User["role"]) => apiFetch<{ user: User }>("/api/v1/admin/users/role", jsonRequest("PATCH", { email, role })),
  reportUrl: (id: string) => `/documents/${encodeURIComponent(id)}/report`,
  profile: (name: string) => apiFetch<{ user: User }>("/api/v1/auth/me", jsonRequest("PATCH", { name })),
  sessions: async () => (await apiFetch<{ sessions: Session[] }>("/api/v1/auth/sessions")).sessions,
  revokeSession: (id: string) => apiFetch<void>(`/api/v1/auth/sessions/${encodeURIComponent(id)}`, { method: "DELETE" }),
  changePassword: (current_password: string, new_password: string, password_confirmation: string) => apiFetch<{ message: string }>("/api/v1/auth/change-password", jsonRequest("POST", { current_password, new_password, password_confirmation })),
  exportData: () => apiFetch<Record<string, unknown>>("/api/v1/auth/export"),
  exportArchive: () => apiFetch<Blob>("/api/v1/auth/export/archive"),
  deleteAccount: (password: string) => apiFetch<void>("/api/v1/auth/me", jsonRequest("DELETE", { password, confirmation: "DELETE" })),
  logout: () => apiFetch<void>("/api/v1/auth/logout", { method: "POST" }),
};
