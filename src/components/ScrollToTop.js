import React, { useEffect, useState } from 'react';
import { ArrowUp } from 'lucide-react';

// Fixed at the bottom right of every page; shows once the page has been scrolled down.
export default function ScrollToTop({ threshold = 400 }) {
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const update = () => setVisible(window.scrollY > threshold);
    update();
    window.addEventListener('scroll', update, { passive: true });
    return () => window.removeEventListener('scroll', update);
  }, [threshold]);
  if (!visible) return null;
  return (
    <button
      type="button"
      aria-label="맨 위로"
      title="맨 위로"
      onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}
      className="fixed bottom-6 right-6 z-50 flex h-12 w-12 items-center justify-center rounded-full border border-border bg-card text-foreground shadow-lg transition hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <ArrowUp size={22} aria-hidden="true" />
    </button>
  );
}
