import { create } from 'zustand';

interface MachineStore {
  selectedMachineOrderId: string | null;
  setSelectedMachineOrderId: (id: string | null) => void;
}

export const useMachineStore = create<MachineStore>((set) => ({
  selectedMachineOrderId: null,
  setSelectedMachineOrderId: (id) => set({ selectedMachineOrderId: id }),
}));
