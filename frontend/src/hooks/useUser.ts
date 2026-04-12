import { useState } from "react";

const STORAGE_KEY = "chess_coach_user";

export interface UserState {
  id: number;
  username: string;
  chess_com_user: string;
  puzzle_rating: number;
}

const DEFAULT: UserState = {
  id: 1,
  username: "player",
  chess_com_user: "",
  puzzle_rating: 1500,
};

export function useUser() {
  const [user, setUser] = useState<UserState>(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      return raw ? JSON.parse(raw) : DEFAULT;
    } catch {
      return DEFAULT;
    }
  });

  const save = (u: UserState) => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(u));
    setUser(u);
  };

  return { user, setUser: save };
}
