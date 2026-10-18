(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const state = {
    file: null,
    documentId: null,
    metadata: null,
    terms: null,
    clauses: null,
    finance: null,
    termsError: null,
    clausesError: null,
    financeError: null,
    uploading: false,
    asking: false,
  };

  const termLabels = {
    loan_amount: "Loan amount",
    interest_rate: "Interest rate",
    tenure: "Loan tenure",
    emi: "Stated EMI",
    processing_fee: "Processing fee",
    late_fee: "Late payment fee",
    interest_type: "Interest type",
  };

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined && text !== null) element.textContent = String(text);
    return element;
  }

  function setSystemStatus(label, kind = "ready") {
    const status = $("systemStatus");
    status.textContent = label;
    status.parentElement.classList.toggle("is-busy", kind === "busy");
    status.parentElement.classList.toggle("is-error", kind === "error");
  }

  function setUploadStatus(message, kind = "info") {
    const status = $("uploadStatus");
    status.textContent = message;
    status.hidden = !message;
    status.classList.toggle("is-error", kind === "error");
    if (kind === "error") setSystemStatus("Action needed", "error");
    else if (!state.uploading) setSystemStatus(state.documentId ? "Analysis ready" : "Ready to analyze");
  }

  function formatBytes(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    const units = ["KB", "MB", "GB"];
    let size = bytes / 1024;
    let unit = 0;
    while (size >= 1024 && unit < units.length - 1) {
      size /= 1024;
      unit += 1;
    }
    return `${size.toLocaleString(undefined, { maximumFractionDigits: size < 10 ? 1 : 0 })} ${units[unit]}`;
  }

  function setSelectedFile(file) {
    if (!file) return;
    if (state.uploading) return;
    const looksLikePdf = file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");
    if (!looksLikePdf) {
      state.file = null;
      $("fileInput").value = "";
      $("fileSummary").hidden = true;
      $("analyzeButton").disabled = true;
      setUploadStatus("This file does not look like a PDF. Choose a loan agreement ending in .pdf.", "error");
      return;
    }
    if (file.size === 0) {
      state.file = null;
      $("fileInput").value = "";
      $("fileSummary").hidden = true;
      $("analyzeButton").disabled = true;
      setUploadStatus("This PDF is empty. Choose a file that contains your agreement.", "error");
      return;
    }

    state.file = file;
    $("fileName").textContent = file.name;
    $("fileSize").textContent = formatBytes(file.size);
    $("fileState").textContent = "Ready";
    $("fileState").classList.remove("is-error");
    $("fileSummary").hidden = false;
    $("analyzeButton").disabled = false;
    setUploadStatus("PDF selected. Start the analysis when you’re ready.");
  }

  function clearSelectedFile() {
    if (state.uploading) return;
    state.file = null;
    $("fileInput").value = "";
    $("fileSummary").hidden = true;
    $("analyzeButton").disabled = true;
    setUploadStatus("");
  }

  function errorFromResponse(response, payload) {
    const detail = payload && payload.detail;
    let message = typeof detail === "string" ? detail : "";
    if (!message && Array.isArray(detail)) {
      message = detail.map((item) => item.msg).filter(Boolean).join(" ");
    }
    if (!message) message = `Request failed (${response.status}).`;
    if (response.status === 413) message = "This PDF is larger than the server allows (10 MB by default). Choose a smaller file and try again.";
    else if (response.status === 415) message = "The uploaded file is not a readable PDF. Choose a valid PDF agreement.";
    else if (response.status === 422 && /selectable text|scanned|no selectable/i.test(message)) {
      message = "We couldn’t find enough selectable text in this PDF. Scanned image-only documents are not supported yet.";
    }
    return new Error(message);
  }

  async function api(path, options = {}) {
    let response;
    try {
      response = await fetch(path, options);
    } catch (error) {
      if (error instanceof TypeError) throw new Error("Could not reach BankEase AI. Check that the app is running, then try again.");
      throw error;
    }
    if (response.status === 204) return null;
    const contentType = response.headers.get("content-type") || "";
    let payload = null;
    if (contentType.includes("application/json")) {
      try { payload = await response.json(); } catch { payload = null; }
    } else if (!response.ok) {
      try { payload = { detail: await response.text() }; } catch { payload = null; }
    }
    if (!response.ok) throw errorFromResponse(response, payload);
    return payload;
  }

  function clearConversation() {
    const conversation = $("conversation");
    conversation.replaceChildren();
    const empty = node("div", "conversation-empty");
    empty.id = "conversationEmpty";
    empty.append(node("span", "conversation-spark", "✳"), node("span", "", "Ask a question to find matching passages in your agreement."));
    conversation.append(empty);
  }

  function toggleUploadBusy(busy) {
    state.uploading = busy;
    $("analyzeButton").disabled = busy || !state.file;
    $("fileInput").disabled = busy;
    $("clearFile").disabled = busy;
    $("dropzone").classList.toggle("is-busy", busy);
    $("dropzone").setAttribute("aria-disabled", String(busy));
    $("uploadProgress").hidden = !busy;
    $("analyzeButton").querySelector("span").textContent = busy ? "Analyzing document" : "Analyze document";
    if (busy) setSystemStatus("Analyzing document", "busy");
  }

  async function analyzeSelectedFile() {
    if (state.uploading) return;
    if (!state.file) {
      setUploadStatus("Choose a PDF before starting the analysis.", "error");
      return;
    }
    const form = new FormData();
    form.append("file", state.file);
    toggleUploadBusy(true);
    setUploadStatus("Your document is being checked. Results will appear when the analysis is complete.");
    let succeeded = false;
    try {
      const metadata = await api("/documents/analyze", { method: "POST", body: form });
      state.documentId = metadata.id;
      state.metadata = metadata;
      state.terms = null;
      state.clauses = null;
      state.finance = null;
      state.termsError = null;
      state.clausesError = null;
      state.financeError = null;
      clearConversation();
      await loadAnalysisResults();
      $("fileState").textContent = "Analyzed";
      setUploadStatus(`Analysis complete for ${metadata.filename || state.file.name}. Check the extracted values and source passages below.`);
      succeeded = true;
    } catch (error) {
      $("fileState").textContent = "Retry available";
      $("fileState").classList.add("is-error");
      setUploadStatus(error.message || "The document could not be analyzed. Try again with a text-based PDF.", "error");
    } finally {
      toggleUploadBusy(false);
      if (succeeded) setSystemStatus("Analysis ready");
    }
  }

  async function loadAnalysisResults() {
    const id = encodeURIComponent(state.documentId);
    const paths = [
      `/documents/${id}/terms`,
      `/documents/${id}/clauses`,
      `/documents/${id}/calculate-emi`,
    ];
    const [terms, clauses, finance] = await Promise.allSettled([
      api(paths[0]),
      api(paths[1]),
      api(paths[2], { method: "POST" }),
    ]);
    state.terms = terms.status === "fulfilled" ? terms.value : null;
    state.termsError = terms.status === "rejected" ? terms.reason.message : null;
    state.clauses = clauses.status === "fulfilled" ? clauses.value : null;
    state.clausesError = clauses.status === "rejected" ? clauses.reason.message : null;
    state.finance = finance.status === "fulfilled" ? finance.value : null;
    state.financeError = finance.status === "rejected" ? finance.reason.message : null;
    renderAnalysis();
  }

  function formatCurrency(value) {
    if (value === null || value === undefined || value === "") return "Not available";
    const number = Number(value);
    if (!Number.isFinite(number)) return "Not available";
    return new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 2 }).format(number);
  }

  function parseNumber(value) {
    const match = String(value ?? "").replace(/,/g, "").match(/-?\d+(?:\.\d+)?/);
    return match ? Number(match[0]) : null;
  }

  function displayTermValue(key, term) {
    if (!term || term.value === null || term.value === undefined || term.raw === null) return { text: "Not found in document", missing: true };
    const raw = String(term.raw).trim();
    if (key === "loan_amount" || key === "emi") return { text: formatCurrency(term.value), missing: false };
    if (key === "interest_rate") return { text: /%/.test(raw) ? raw : `${raw}%`, missing: false };
    if (key === "processing_fee" || key === "late_fee") {
      if (/^(?:rs\.?|inr|₹)/i.test(raw)) return { text: formatCurrency(parseNumber(raw)), missing: false };
      return { text: raw, missing: false };
    }
    if (key === "interest_type") return { text: raw.charAt(0).toUpperCase() + raw.slice(1), missing: false };
    return { text: raw, missing: false };
  }

  function statusLabel(status) {
    if (status === "extracted") return "Found";
    if (status === "review" || status === "needs_review") return "Review";
    if (status === "missing" || status === "not_found") return "Missing";
    return status || "Available";
  }

  function emptyState(title, description, isError = false) {
    const wrap = node("div", `empty-state inline-empty${isError ? " is-error" : ""}`);
    wrap.append(node("span", "empty-state-icon", isError ? "!" : "◎"));
    const copy = node("div");
    copy.append(node("b", "", title), node("p", "", description));
    wrap.append(copy);
    return wrap;
  }

  function renderTerms() {
    const container = $("termsContent");
    container.replaceChildren();
    if (state.termsError) {
      container.append(emptyState("Terms could not be loaded", state.termsError, true));
      $("termsCount").hidden = true;
      return;
    }
    if (!state.terms || typeof state.terms !== "object" || !Object.keys(state.terms).length) {
      container.append(emptyState("No term fields were returned", "The analysis completed, but no extractable loan fields were provided for this document."));
      $("termsCount").hidden = true;
      return;
    }

    const entries = Object.entries(state.terms);
    const count = entries.filter(([, value]) => value && value.value !== null && value.value !== undefined).length;
    $("termsCount").textContent = `${count} of ${entries.length} found`;
    $("termsCount").hidden = false;
    for (const [key, term] of entries) {
      const card = node("article", "term-card");
      const top = node("div", "term-top");
      top.append(node("span", "term-label", termLabels[key] || key.replaceAll("_", " ")));
      const badge = node("span", `term-badge ${term?.status === "extracted" ? "extracted" : term?.status === "review" ? "review" : ""}`, statusLabel(term?.status));
      top.append(badge);
      const value = displayTermValue(key, term);
      card.append(top, node("p", `term-value${value.missing ? " is-missing" : ""}`, value.text));
      if (term?.note) card.append(node("p", "term-note", term.note));
      if (term?.evidence) {
        const details = node("details", "term-source");
        const summary = node("summary", "", term.page ? `View source · page ${term.page}` : "View source text");
        details.append(summary, node("p", "", term.evidence));
        if (term.page) details.append(node("small", "", `Source page ${term.page}`));
        card.append(details);
      } else if (term?.page) {
        card.append(node("small", "term-source", `Source page ${term.page}`));
      }
      container.append(card);
    }
  }

  function renderClauses() {
    const container = $("clausesContent");
    container.replaceChildren();
    if (state.clausesError) {
      container.append(emptyState("Clause findings could not be loaded", state.clausesError, true));
      $("clauseFootnote").hidden = true;
      return;
    }
    if (!Array.isArray(state.clauses)) {
      container.append(emptyState("No clauses scanned yet", "After analysis, matching passages and their page references will be shown here."));
      $("clauseFootnote").hidden = true;
      return;
    }
    if (!state.clauses.length) {
      container.append(emptyState("No matching clauses returned", "The analysis did not identify passages in the supported clause categories."));
      $("clauseFootnote").hidden = false;
      return;
    }
    for (const clause of state.clauses) {
      const card = node("article", "clause-card");
      const heading = node("div", "clause-title-row");
      heading.append(node("span", "clause-category", clause.category || "Document clause"));
      heading.append(node("span", "clause-page", clause.page ? `Page ${clause.page}${clause.section ? ` · ${clause.section}` : ""}` : "Page not provided"));
      card.append(heading);
      if (clause.explanation) card.append(node("p", "clause-explanation", clause.explanation));
      if (clause.attention_point) {
        const attention = node("p", "attention-copy");
        attention.append(node("b", "", "Review prompt · "), document.createTextNode(clause.attention_point));
        card.append(attention);
      }
      if (clause.text || clause.evidence) {
        const details = node("details", "clause-evidence");
        details.append(node("summary", "", "View clause text"), node("p", "", clause.evidence || clause.text));
        card.append(details);
      }
      container.append(card);
    }
    $("clauseFootnote").hidden = false;
  }

  function renderFinancial() {
    const container = $("financialContent");
    container.replaceChildren();
    if (state.financeError) {
      const error = node("div", "financial-error");
      error.append(node("b", "", "Repayment summary unavailable"));
      error.append(document.createTextNode(`The calculation needs valid principal, interest rate, and tenure values. ${state.financeError}`));
      container.append(error);
      return;
    }
    const finance = state.finance;
    if (!finance) {
      const empty = node("div", "empty-state sidebar-empty");
      empty.append(node("span", "empty-state-icon", "₹"), node("b", "", "Your repayment summary"), node("p", "", "Once your loan terms are extracted, we’ll calculate an EMI using the document values."));
      container.append(empty);
      return;
    }

    const metrics = node("div", "finance-metrics");
    const items = [
      ["Calculated monthly EMI", finance.calculated_emi, true],
      ["Total repayment", finance.total_repayment, false],
      ["Total interest", finance.total_interest, false],
    ];
    for (const [label, value, primary] of items) {
      const metric = node("div", `finance-metric${primary ? " primary-metric" : ""}`);
      metric.append(node("span", "", label), node("strong", "", formatCurrency(value)));
      metrics.append(metric);
    }
    container.append(metrics);

    const principal = state.terms?.loan_amount?.value;
    const interest = finance.total_interest;
    if (principal !== null && principal !== undefined && Number.isFinite(Number(principal)) && Number.isFinite(Number(interest)) && Number(principal) >= 0 && Number(interest) >= 0) {
      const total = Number(principal) + Number(interest);
      if (total > 0) {
        const breakdown = node("div", "finance-breakdown");
        const line = node("div", "breakdown-top");
        line.append(node("span", "", "Principal and interest"), node("span", "", formatCurrency(total)));
        const track = node("div", "breakdown-track");
        track.setAttribute("role", "img");
        track.setAttribute("aria-label", `Principal ${formatCurrency(principal)}, interest ${formatCurrency(interest)}`);
        const principalPart = node("span", "breakdown-principal");
        principalPart.style.width = `${(Number(principal) / total) * 100}%`;
        const interestPart = node("span", "breakdown-interest");
        interestPart.style.width = `${(Number(interest) / total) * 100}%`;
        track.append(principalPart, interestPart);
        const legend = node("div", "breakdown-legend");
        const pLegend = node("span", "legend-item");
        pLegend.append(node("i", "legend-dot"), document.createTextNode(`Principal ${formatCurrency(principal)}`));
        const iLegend = node("span", "legend-item");
        iLegend.append(node("i", "legend-dot interest"), document.createTextNode(`Interest ${formatCurrency(interest)}`));
        legend.append(pLegend, iLegend);
        breakdown.append(line, track, legend);
        container.append(breakdown);
      }
    }

    if (finance.note) container.append(node("p", "finance-note", finance.note));
    if (Array.isArray(finance.assumptions) && finance.assumptions.length) {
      const details = node("details", "assumption-details");
      const list = node("ul");
      finance.assumptions.forEach((assumption) => list.append(node("li", "", assumption)));
      details.append(node("summary", "", "Calculation assumptions"), list);
      container.append(details);
    }
  }

  function renderMetadata() {
    const container = $("documentMeta");
    container.replaceChildren();
    const meta = state.metadata;
    if (!meta) {
      container.hidden = true;
      return;
    }
    const labels = [meta.doc_type, meta.loan_category ? `${meta.loan_category} loan` : null, meta.page_count ? `${meta.page_count} ${meta.page_count === 1 ? "page" : "pages"}` : null];
    labels.filter(Boolean).forEach((label) => container.append(node("span", "meta-chip", label.replaceAll("_", " "))));
    if (Array.isArray(meta.unreadable_pages) && meta.unreadable_pages.length) {
      container.append(node("span", "meta-chip", `Text not detected on page${meta.unreadable_pages.length > 1 ? "s" : ""} ${meta.unreadable_pages.join(", ")}`));
    }
    if (state.documentId) {
      const reportChip = node("span", "meta-chip");
      const report = node("a", "", "Printable report");
      report.href = `/documents/${encodeURIComponent(state.documentId)}/report`;
      report.target = "_blank";
      report.rel = "noopener noreferrer";
      reportChip.append(report);
      container.append(reportChip);
    }
    container.hidden = container.childElementCount === 0;
  }

  function renderAnalysis() {
    renderMetadata();
    renderTerms();
    renderClauses();
    renderFinancial();
    $("questionHelp").textContent = state.documentId
      ? "Answers quote matching passages from this document. They are not generated legal advice."
      : "Analyze a document before sending a question. Answers quote matching passages; they are not generated legal advice.";
    $("askButton").disabled = !state.documentId || state.asking;
  }

  function addChatMessage(kind, content, options = {}) {
    const conversation = $("conversation");
    const empty = $("conversationEmpty");
    if (empty && empty.parentElement === conversation) empty.remove();
    const wrapper = node("div", `chat-message ${kind}${options.error ? " error" : ""}`);
    wrapper.append(node("span", "chat-role", kind === "user" ? "You" : "BankEase · source-based"));
    const bubble = node("div", "chat-bubble", content);
    wrapper.append(bubble);
    if (Array.isArray(options.sources) && options.sources.length) {
      const list = node("div", "source-list");
      options.sources.forEach((source) => {
        const item = node("div", "source-item");
        item.append(node("b", "", source.page ? `Source · page ${source.page}` : "Source passage"), document.createTextNode(source.text || ""));
        list.append(item);
      });
      wrapper.append(list);
    }
    conversation.append(wrapper);
    conversation.scrollTop = conversation.scrollHeight;
    return wrapper;
  }

  async function askQuestion(question) {
    const text = question.trim();
    if (!text) {
      $("questionInput").setCustomValidity("Enter a question about your loan.");
      $("questionInput").reportValidity();
      return;
    }
    $("questionInput").setCustomValidity("");
    if (text.length < 3) {
      addChatMessage("assistant", "Please enter at least 3 characters so I can search the agreement.", { error: true });
      return;
    }
    if (text.length > 500) {
      addChatMessage("assistant", "Questions can contain up to 500 characters. Shorten your question and try again.", { error: true });
      return;
    }
    if (!state.documentId) {
      addChatMessage("assistant", "Analyze a loan document first, then I can search its text for an answer.", { error: true });
      return;
    }
    if (state.asking) return;

    state.asking = true;
    $("askButton").disabled = true;
    addChatMessage("user", text);
    const pending = addChatMessage("assistant", "Searching the agreement for matching passages…");
    try {
      const result = await api(`/documents/${encodeURIComponent(state.documentId)}/ask`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: text }),
      });
      pending.remove();
      const answer = result?.answer || "The document did not return an answer.";
      addChatMessage("assistant", answer, { sources: result?.sources || [] });
      $("questionInput").value = "";
      resizeQuestionInput();
    } catch (error) {
      pending.remove();
      addChatMessage("assistant", error.message || "The question could not be answered. Try again.", { error: true });
    } finally {
      state.asking = false;
      $("askButton").disabled = !state.documentId;
      $("questionInput").focus();
    }
  }

  function resizeQuestionInput() {
    const input = $("questionInput");
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 115)}px`;
  }

  $("fileInput").addEventListener("change", (event) => setSelectedFile(event.target.files?.[0]));
  $("clearFile").addEventListener("click", clearSelectedFile);
  $("analyzeButton").addEventListener("click", analyzeSelectedFile);
  $("dropzone").addEventListener("dragover", (event) => {
    event.preventDefault();
    if (!state.uploading) $("dropzone").classList.add("is-dragging");
  });
  $("dropzone").addEventListener("dragleave", () => $("dropzone").classList.remove("is-dragging"));
  $("dropzone").addEventListener("drop", (event) => {
    event.preventDefault();
    $("dropzone").classList.remove("is-dragging");
    if (!state.uploading) setSelectedFile(event.dataTransfer?.files?.[0]);
  });
  $("questionForm").addEventListener("submit", (event) => {
    event.preventDefault();
    askQuestion($("questionInput").value);
  });
  $("questionInput").addEventListener("input", resizeQuestionInput);
  $("questionInput").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      $("questionForm").requestSubmit();
    }
  });
  document.querySelectorAll(".suggestion-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      $("questionInput").value = chip.textContent.trim();
      resizeQuestionInput();
      $("questionInput").focus();
    });
  });
  document.querySelectorAll(".nav-link").forEach((link) => {
    link.addEventListener("click", () => {
      document.querySelectorAll(".nav-link").forEach((item) => item.classList.remove("is-active"));
      link.classList.add("is-active");
    });
  });
})();
