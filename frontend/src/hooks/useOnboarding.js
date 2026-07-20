import { useState, useEffect } from 'react';

const STORAGE_KEY = 'titibet_onboarding_v1';

export function useOnboarding(user) {
  const [show, setShow] = useState(false);

  useEffect(() => {
    if (!user) return;
    const done = localStorage.getItem(STORAGE_KEY);
    if (!done) setShow(true);
  }, [user]);

  const complete = () => {
    localStorage.setItem(STORAGE_KEY, 'true');
    setShow(false);
  };

  return { showOnboarding: show, completeOnboarding: complete };
}
