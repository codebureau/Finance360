import { useMutation, useQueryClient } from "@tanstack/react-query";
import { LogOut } from "lucide-react";

import { Button } from "@/components/ui/button";
import { logOut } from "@/lib/auth";

/** Sign out. Security lives under Settings; this stays self-contained so the shell only places it. */
export function AccountMenu() {
  const queryClient = useQueryClient();
  const signOut = useMutation({
    mutationFn: logOut,
    onSettled: () => {
      // Nothing of the signed-out Session's data stays in memory.
      queryClient.removeQueries({ predicate: (q) => q.queryKey[0] !== "auth-status" });
      return queryClient.invalidateQueries({ queryKey: ["auth-status"] });
    },
  });

  return (
    <Button variant="outline" onClick={() => signOut.mutate()} disabled={signOut.isPending}>
      <LogOut size={16} />
      Sign out
    </Button>
  );
}
