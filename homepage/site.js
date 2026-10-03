// LiNotes website – small, local, no libraries, no tracking.
(() => {
  const $ = (s, root = document) => root.querySelector(s);
  const $$ = (s, root = document) => [...root.querySelectorAll(s)];
  const dark = window.matchMedia("(prefers-color-scheme: dark)");

  // Navigation gets a line once the page scrolls.
  const nav = $(".nav");
  const onScroll = () => nav && nav.classList.toggle("scrolled", window.scrollY > 8);
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();

  // Fade in when scrolled into view.
  const reveal = new IntersectionObserver((entries) => {
    for (const entry of entries) if (entry.isIntersecting) { entry.target.classList.add("in"); reveal.unobserve(entry.target); }
  }, { rootMargin: "0px 0px -8% 0px", threshold: 0.08 });
  $$(".reveal").forEach((el) => reveal.observe(el));

  // Hero: the window tilts upright while scrolling (like Apple's product pages).
  const stage = $(".hero-stage");
  if (stage && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    const win = $(".window", stage);
    const tilt = () => {
      const rect = stage.getBoundingClientRect();
      const progress = Math.min(1, Math.max(0, 1 - rect.top / (window.innerHeight * 0.8)));
      win.style.setProperty("--tilt", `${(8 * (1 - progress)).toFixed(2)}deg`);
      win.style.setProperty("--scale", (0.96 + 0.04 * progress).toFixed(3));
    };
    window.addEventListener("scroll", tilt, { passive: true });
    tilt();
  }

  // Story: the sticky picture follows the text step in view.
  for (const story of $$(".story")) {
    const steps = $$(".step", story);
    const frames = $$(".stage .window", story);
    const show = (index) => {
      steps.forEach((s, i) => s.classList.toggle("active", i === index));
      frames.forEach((f, i) => f.classList.toggle("show", i === index));
    };
    const watcher = new IntersectionObserver((entries) => {
      for (const entry of entries) if (entry.isIntersecting) show(steps.indexOf(entry.target));
    }, { rootMargin: "-45% 0px -45% 0px" });
    steps.forEach((s) => watcher.observe(s));
    show(0);
  }

  // Picture with light and dark variant: data-light / data-dark.
  const pick = (el) => (dark.matches && el.dataset.dark) ? el.dataset.dark : el.dataset.light;
  const applyTheme = () => $$("img[data-light]").forEach((img) => { img.src = pick(img); });
  dark.addEventListener("change", applyTheme);
  applyTheme();

  // Segmented controls swap a showcase (plans, platforms).
  for (const group of $$("[data-showcase]")) {
    const target = $(group.dataset.showcase);
    const buttons = $$("button", group);
    buttons.forEach((button) => button.addEventListener("click", () => {
      buttons.forEach((b) => b.classList.toggle("on", b === button));
      target.classList.add("fading");
      setTimeout(() => {
        for (const [key, value] of Object.entries(button.dataset)) {
          const el = $(`[data-slot="${key}"]`, target);
          if (!el) continue;
          if (el.tagName === "IMG") {
            const [light, darkSrc] = value.split("|");
            el.dataset.light = light; el.dataset.dark = darkSrc || light; el.src = pick(el);
          } else el.textContent = value;
        }
        target.classList.remove("fading");
      }, 280);
    }));
  }

  // Carousel arrows.
  for (const nav of $$(".carousel-nav")) {
    const track = $(nav.dataset.track);
    $$("button", nav).forEach((b) => b.addEventListener("click", () => track.scrollBy({ left: Number(b.dataset.dir) * 280, behavior: "smooth" })));
  }

  // Click a picture to see it large.
  const box = document.createElement("div");
  box.className = "lightbox";
  box.innerHTML = "<img alt=''>";
  document.body.appendChild(box);
  box.addEventListener("click", () => box.classList.remove("open"));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") box.classList.remove("open"); });
  document.addEventListener("click", (e) => {
    const img = e.target.closest("img.zoomable");
    if (!img) return;
    $("img", box).src = img.currentSrc || img.src;
    $("img", box).alt = img.alt;
    box.classList.add("open");
  });

  // Copy buttons on terminal blocks.
  for (const term of $$(".terminal[data-copy]")) {
    const button = document.createElement("button");
    button.className = "copy";
    button.textContent = "Kopieren";
    button.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(term.dataset.copy);
        button.textContent = "Kopiert ✓"; button.classList.add("done");
      } catch (error) { button.textContent = "Bitte markieren"; }
      setTimeout(() => { button.textContent = "Kopieren"; button.classList.remove("done"); }, 1800);
    });
    term.appendChild(button);
  }

  // Docs: table of contents follows the reading position; search filters the sections.
  const toc = $(".docs nav.toc");
  if (toc) {
    const links = $$("a[href^='#']", toc);
    const sections = links.map((a) => document.getElementById(a.getAttribute("href").slice(1))).filter(Boolean);
    const spy = new IntersectionObserver((entries) => {
      for (const entry of entries) if (entry.isIntersecting) {
        links.forEach((a) => a.classList.toggle("active", a.getAttribute("href") === "#" + entry.target.id));
      }
    }, { rootMargin: "-20% 0px -70% 0px" });
    sections.forEach((s) => spy.observe(s));
    const search = $(".search", toc);
    if (search) search.addEventListener("input", () => {
      const words = search.value.toLowerCase().trim().split(/\s+/).filter(Boolean);
      for (const section of sections) {
        const text = section.textContent.toLowerCase();
        const hit = words.every((w) => text.includes(w));
        section.classList.toggle("hidden", !hit);
        const link = links.find((a) => a.getAttribute("href") === "#" + section.id);
        if (link) link.style.display = hit ? "" : "none";
      }
    });
  }
})();
