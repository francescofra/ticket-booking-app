(function () {
  "use strict";

  const modal = document.getElementById("modal");
  const bookingView = document.getElementById("booking-view");
  const processingView = document.getElementById("processing-view");
  const confirmationView = document.getElementById("confirmation-view");
  const form = document.getElementById("booking-form");
  const modalConcert = document.getElementById("modal-concert");
  const formTotal = document.getElementById("form-total");
  const formError = document.getElementById("form-error");
  const submitBtn = document.getElementById("submit-btn");

  let currentPrice = 0;

  function showView(view) {
    [bookingView, processingView, confirmationView].forEach((v) => v.classList.add("hidden"));
    view.classList.remove("hidden");
  }

  function openModal(concertId, artist, price) {
    currentPrice = parseFloat(price) || 0;
    form.reset();
    form.elements["concert_id"].value = concertId;
    form.elements["quantity"].value = 1;
    modalConcert.textContent = artist;
    formError.hidden = true;
    formError.textContent = "";
    updateTotal();
    showView(bookingView);
    modal.classList.remove("hidden");
    modal.setAttribute("aria-hidden", "false");
    setTimeout(() => form.elements["name"].focus(), 50);
  }

  function closeModal() {
    modal.classList.add("hidden");
    modal.setAttribute("aria-hidden", "true");
  }

  function updateTotal() {
    const qty = parseInt(form.elements["quantity"].value || "0", 10);
    const total = Math.max(0, qty) * currentPrice;
    formTotal.textContent = "Total: € " + total.toFixed(2);
  }

  function refreshCardSeats(concertId, newAvailable) {
    const card = document.querySelector('.card[data-concert-id="' + concertId + '"]');
    if (!card) return;
    const seatsEl = card.querySelector(".card-seats");
    const btn = card.querySelector(".book-btn");
    if (seatsEl) {
      seatsEl.dataset.seats = String(newAvailable);
      seatsEl.textContent = newAvailable > 0 ? newAvailable + " seats left" : "Sold out";
    }
    if (btn) btn.disabled = newAvailable <= 0;
  }

  async function refreshAllSeats() {
    try {
      const r = await fetch("/api/concerts", { headers: { Accept: "application/json" } });
      if (!r.ok) return;
      const data = await r.json();
      (data.concerts || []).forEach((c) => refreshCardSeats(c.id, c.available_seats));
    } catch (_) { /* network blips ok */ }
  }

  // Wire up "Book tickets" buttons.
  document.querySelectorAll(".book-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      openModal(btn.dataset.id, btn.dataset.artist, btn.dataset.price);
    });
  });

  // Close handlers.
  document.querySelectorAll("[data-close]").forEach((el) => {
    el.addEventListener("click", closeModal);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !modal.classList.contains("hidden")) closeModal();
  });

  // Live total recompute.
  form.elements["quantity"].addEventListener("input", updateTotal);

  // Submit.
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    formError.hidden = true;
    formError.textContent = "";

    const payload = {
      concert_id: form.elements["concert_id"].value,
      quantity: parseInt(form.elements["quantity"].value, 10),
      name: form.elements["name"].value.trim(),
      email: form.elements["email"].value.trim(),
      payment_token: form.elements["payment_token"].value.trim(),
    };

    submitBtn.disabled = true;
    showView(processingView);

    try {
      const r = await fetch("/api/book", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await r.json();
      if (!r.ok) {
        const reason = (data && data.detail) || "booking_failed";
        const human = {
          sold_out_or_insufficient_seats: "Sorry, not enough seats available.",
          concert_not_found: "This concert no longer exists.",
          payment_processing_failed: "Payment processing failed. Please try again.",
        }[reason] || ("Error: " + reason);
        formError.textContent = human;
        formError.hidden = false;
        showView(bookingView);
        await refreshAllSeats();
        return;
      }

      // Populate confirmation view.
      document.getElementById("conf-artist").textContent =
        data.concert.artist + " · " + data.concert.date;
      document.getElementById("conf-ticket").textContent = data.ticket_id;
      document.getElementById("conf-holder").textContent = data.holder_name;
      document.getElementById("conf-qty").textContent = String(data.quantity);
      document.getElementById("conf-total").textContent =
        "€ " + Number(data.total_eur).toFixed(2);
      document.getElementById("conf-pod").textContent = data.pod_id;
      document.getElementById("conf-qr").src =
        "data:image/png;base64," + data.qr_png_base64;

      showView(confirmationView);
      await refreshAllSeats();
    } catch (err) {
      formError.textContent = "Network error. Please try again.";
      formError.hidden = false;
      showView(bookingView);
    } finally {
      submitBtn.disabled = false;
    }
  });
})();
