(() => {
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ------------------------------------------------------------- falling snow
  const canvas = document.getElementById("snow");
  if (canvas && !reduceMotion) {
    const ctx = canvas.getContext("2d");
    let w = 0, h = 0;
    const resize = () => {
      w = canvas.width = window.innerWidth;
      h = canvas.height = window.innerHeight;
    };
    resize();
    window.addEventListener("resize", resize);

    const flake = () => ({
      x: Math.random() * w,
      y: Math.random() * h,
      r: Math.random() * 2.4 + 0.8,
      speed: Math.random() * 0.7 + 0.3,
      drift: Math.random() * Math.PI * 2,
    });
    const flakes = Array.from({ length: Math.min(150, Math.floor(w / 7)) }, flake);

    const tick = () => {
      ctx.clearRect(0, 0, w, h);
      ctx.fillStyle = "rgba(255, 255, 255, 0.85)";
      ctx.beginPath();
      for (const f of flakes) {
        f.y += f.speed;
        f.drift += 0.01;
        f.x += Math.sin(f.drift) * 0.4;
        if (f.y > h + 5) { f.y = -5; f.x = Math.random() * w; }
        ctx.moveTo(f.x, f.y);
        ctx.arc(f.x, f.y, f.r, 0, Math.PI * 2);
      }
      ctx.fill();
      requestAnimationFrame(tick);
    };
    tick();
  }

  // ------------------------------------------------------------- copy buttons
  document.querySelectorAll("[data-copy]").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.preventDefault();
      const input = document.querySelector(btn.dataset.copy);
      try {
        await navigator.clipboard.writeText(input.value);
      } catch {
        input.select();
        document.execCommand("copy");
      }
      const label = btn.textContent;
      btn.textContent = "Copied ✓";
      setTimeout(() => { btn.textContent = label; }, 1600);
    });
  });

  // -------------------------------------------------------- confirm dialogs
  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (e) => {
      if (!window.confirm(form.dataset.confirm)) e.preventDefault();
    });
  });

  // ------------------------------------------------------------ gift reveal
  const revealForm = document.getElementById("reveal-form");
  if (revealForm) {
    revealForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const gift = revealForm.querySelector(".gift");
      if (gift.disabled) return;
      gift.disabled = true;

      let data;
      try {
        const res = await fetch(revealForm.action, {
          method: "POST",
          body: new FormData(revealForm),
          headers: { Accept: "application/json" },
        });
        if (!res.ok) throw new Error(res.status);
        data = await res.json();
      } catch {
        revealForm.submit(); // plain form post as a fallback
        return;
      }

      gift.classList.add("open");
      if (!reduceMotion) confetti(gift);

      setTimeout(() => {
        document.getElementById("receiver-name").textContent = data.name;
        const wishlist = document.getElementById("receiver-wishlist");
        if (data.wishlist) {
          wishlist.querySelector(".wishlist-text").textContent = data.wishlist;
          wishlist.hidden = false;
        }
        document.getElementById("reveal-intro").hidden = true;
        document.getElementById("reveal-result").hidden = false;
      }, reduceMotion ? 0 : 750);
    });
  }

  function confetti(origin) {
    const rect = origin.getBoundingClientRect();
    const cx = rect.left + rect.width / 2;
    const cy = rect.top + rect.height / 3;
    const colors = ["#e5383b", "#ffc94a", "#1fa37a", "#ffffff", "#7bdff2"];
    for (let i = 0; i < 110; i++) {
      const piece = document.createElement("span");
      piece.className = "confetti";
      piece.style.background = colors[i % colors.length];
      piece.style.left = `${cx}px`;
      piece.style.top = `${cy}px`;
      document.body.appendChild(piece);

      const angle = Math.random() * Math.PI * 2;
      const dist = 120 + Math.random() * 240;
      const dx = Math.cos(angle) * dist;
      const dy = Math.sin(angle) * dist - 140;
      piece.animate(
        [
          { transform: "translate(0, 0) rotate(0deg)", opacity: 1 },
          { transform: `translate(${dx}px, ${dy + 380}px) rotate(${Math.random() * 900}deg)`, opacity: 0 },
        ],
        { duration: 1500 + Math.random() * 1000, easing: "cubic-bezier(.2, .7, .4, 1)" }
      ).onfinish = () => piece.remove();
    }
  }
})();
