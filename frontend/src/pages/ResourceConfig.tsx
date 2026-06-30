// frontend/src/pages/ResourceConfig.tsx
//
// Configurazione delle RISORSE a capacità di gruppo (resource_types).
// Una risorsa NON è un individuo: è un tipo (workcenter, skill, ore/giorno, count).
// Capacità di gruppo = count × ore/giorno. Lo scheduler alloca su questa capacità.

import { Fragment, useMemo, useState } from "react";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectItem } from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Users, Plus, Trash2, Save, CalendarDays } from "lucide-react";
import {
  useResourceTypes,
  useWorkcenters,
  useCreateResourceType,
  useUpdateResourceType,
  useDeleteResourceType,
  type SkillType,
  type ResourceType,
  type WeekdaySchedule,
} from "@/api/hooks/useResourceTypes";

const SKILLS: SkillType[] = ["ELECTRICAL", "MECHANICAL", "MULTI"];
const WEEKDAYS = ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"];

/** Schedule effettivo di un tipo risorsa: dal weekday_schedule o default (lun–ven, weekend 0). */
function effectiveSchedule(r: ResourceType): WeekdaySchedule {
  const out: WeekdaySchedule = {};
  for (let wd = 0; wd < 7; wd++) {
    const e = r.weekday_schedule?.[String(wd)];
    if (e) out[String(wd)] = { count: e.count, hours: e.hours };
    else if (wd < 5) out[String(wd)] = { count: r.count, hours: r.daily_capacity_hours };
    else out[String(wd)] = { count: 0, hours: 0 };
  }
  return out;
}

export default function ResourceConfig(): JSX.Element {
  const { data: resources = [], isLoading } = useResourceTypes();
  const { data: workcenters = [] } = useWorkcenters();
  const createMut = useCreateResourceType();
  const updateMut = useUpdateResourceType();
  const deleteMut = useDeleteResourceType();

  const wcById = useMemo(
    () => Object.fromEntries(workcenters.map((w) => [w.id, w])),
    [workcenters],
  );

  // Form "nuovo tipo risorsa"
  const [newWc, setNewWc] = useState("");
  const [newSkill, setNewSkill] = useState<SkillType>("MECHANICAL");
  const [newHours, setNewHours] = useState("8");
  const [newCount, setNewCount] = useState("1");
  const [error, setError] = useState<string | null>(null);

  // Edit inline per riga: id → { hours, count }
  const [edits, setEdits] = useState<Record<string, { hours: string; count: string }>>({});

  // Editor disponibilità settimanale per riga espansa
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [wdDraft, setWdDraft] = useState<WeekdaySchedule>({});

  const openWeekly = (r: ResourceType) => {
    if (expandedId === r.id) {
      setExpandedId(null);
      return;
    }
    setExpandedId(r.id);
    setWdDraft(effectiveSchedule(r));
  };
  const setWd = (wd: number, field: "count" | "hours", value: string) => {
    setWdDraft((p) => ({
      ...p,
      [String(wd)]: { ...p[String(wd)], [field]: parseFloat(value) || 0 },
    }));
  };
  const saveWeekly = (id: string) => {
    updateMut.mutate({ id, weekday_schedule: wdDraft });
    setExpandedId(null);
  };

  const handleCreate = async () => {
    setError(null);
    if (!newWc) {
      setError("Seleziona un workcenter");
      return;
    }
    try {
      await createMut.mutateAsync({
        name: null,
        workcenter_id: newWc,
        skill: newSkill,
        daily_capacity_hours: parseFloat(newHours) || 0,
        count: parseInt(newCount, 10) || 0,
        weekday_schedule: null,
        is_active: true,
      });
      setNewHours("8");
      setNewCount("1");
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? "Errore nella creazione");
    }
  };

  const totalDailyMin = resources
    .filter((r) => r.is_active)
    .reduce((acc, r) => acc + r.daily_capacity_hours * 60 * r.count, 0);

  return (
    <div className="space-y-4 max-w-5xl p-4">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Users className="h-5 w-5" />
            Risorse — capacità di gruppo
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm leading-relaxed">
          <p>
            Una risorsa è un <strong>tipo</strong>, non una persona:{" "}
            <code className="font-mono bg-stone-100 dark:bg-stone-800 px-1 rounded">(workcenter, skill, ore/giorno, count)</code>.
            La capacità del gruppo è <code className="font-mono bg-stone-100 dark:bg-stone-800 px-1 rounded">count × ore/giorno</code>:
            due risorse da 8h = 16h/giorno. Lo scheduler alloca le operazioni su questa capacità.
          </p>
          <div className="flex gap-2 flex-wrap">
            <Badge variant="outline">{resources.length} tipi configurati</Badge>
            <Badge variant="outline">
              capacità attiva totale: {Math.round(totalDailyMin / 60)} h/giorno
            </Badge>
          </div>
        </CardContent>
      </Card>

      {/* Nuovo tipo risorsa */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Plus className="h-4 w-4" /> Aggiungi tipo risorsa
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-5 gap-3 items-end">
            <div className="md:col-span-2">
              <label className="text-xs font-semibold text-muted-foreground">Workcenter</label>
              <Select value={newWc} onValueChange={(v) => setNewWc(v ?? "")}>
                <SelectItem value="">— seleziona —</SelectItem>
                {workcenters.map((w) => (
                  <SelectItem key={w.id} value={w.id}>
                    {w.code} · {w.name}
                  </SelectItem>
                ))}
              </Select>
            </div>
            <div>
              <label className="text-xs font-semibold text-muted-foreground">Skill</label>
              <Select value={newSkill} onValueChange={(v) => setNewSkill((v as SkillType) ?? "MECHANICAL")}>
                {SKILLS.map((s) => (
                  <SelectItem key={s} value={s}>{s}</SelectItem>
                ))}
              </Select>
            </div>
            <div>
              <label className="text-xs font-semibold text-muted-foreground">Ore/giorno</label>
              <Input type="number" min="0" step="0.5" value={newHours}
                     onChange={(e) => setNewHours(e.target.value)} />
            </div>
            <div className="flex gap-2">
              <div className="flex-1">
                <label className="text-xs font-semibold text-muted-foreground">Count</label>
                <Input type="number" min="0" step="1" value={newCount}
                       onChange={(e) => setNewCount(e.target.value)} />
              </div>
              <Button onClick={handleCreate} disabled={createMut.isPending} className="self-end">
                <Plus className="h-4 w-4 mr-1" /> Aggiungi
              </Button>
            </div>
          </div>
          {error && <p className="text-xs text-red-600 mt-2">{error}</p>}
        </CardContent>
      </Card>

      {/* Tabella */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Tipi risorsa configurati</CardTitle>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <p className="text-sm text-muted-foreground">Caricamento…</p>
          ) : resources.length === 0 ? (
            <p className="text-sm text-muted-foreground">Nessun tipo risorsa configurato.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Workcenter</TableHead>
                  <TableHead>Skill</TableHead>
                  <TableHead className="w-28">Ore/giorno</TableHead>
                  <TableHead className="w-24">Count</TableHead>
                  <TableHead>Capacità gruppo</TableHead>
                  <TableHead>Attivo</TableHead>
                  <TableHead className="text-right">Azioni</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {resources.map((r) => {
                  const edit = edits[r.id];
                  const hours = edit?.hours ?? String(r.daily_capacity_hours);
                  const count = edit?.count ?? String(r.count);
                  const changed =
                    parseFloat(hours) !== r.daily_capacity_hours ||
                    parseInt(count, 10) !== r.count;
                  const groupCap = (parseFloat(hours) || 0) * (parseInt(count, 10) || 0);
                  return (
                    <Fragment key={r.id}>
                    <TableRow>
                      <TableCell className="font-mono text-xs">
                        {wcById[r.workcenter_id]?.code ?? r.workcenter_id.slice(0, 8)}
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline">{r.skill}</Badge>
                      </TableCell>
                      <TableCell>
                        <Input type="number" min="0" step="0.5" value={hours}
                               onChange={(e) =>
                                 setEdits((p) => ({ ...p, [r.id]: { hours: e.target.value, count } }))
                               } />
                      </TableCell>
                      <TableCell>
                        <Input type="number" min="0" step="1" value={count}
                               onChange={(e) =>
                                 setEdits((p) => ({ ...p, [r.id]: { hours, count: e.target.value } }))
                               } />
                      </TableCell>
                      <TableCell className="text-sm font-semibold">{groupCap} h/giorno</TableCell>
                      <TableCell>
                        <Button
                          variant={r.is_active ? "default" : "outline"}
                          size="sm"
                          onClick={() => updateMut.mutate({ id: r.id, is_active: !r.is_active })}
                        >
                          {r.is_active ? "Attivo" : "Inattivo"}
                        </Button>
                      </TableCell>
                      <TableCell className="text-right space-x-1 whitespace-nowrap">
                        <Button
                          variant={expandedId === r.id ? "default" : "outline"}
                          size="sm"
                          title="Disponibilità per giorno"
                          onClick={() => openWeekly(r)}
                        >
                          <CalendarDays className="h-4 w-4" />
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={!changed || updateMut.isPending}
                          title="Salva ore/count base"
                          onClick={() => {
                            updateMut.mutate({
                              id: r.id,
                              daily_capacity_hours: parseFloat(hours) || 0,
                              count: parseInt(count, 10) || 0,
                            });
                            setEdits((p) => {
                              const { [r.id]: _, ...rest } = p;
                              return rest;
                            });
                          }}
                        >
                          <Save className="h-4 w-4" />
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          title="Elimina"
                          onClick={() => {
                            if (confirm("Eliminare questo tipo risorsa?")) deleteMut.mutate(r.id);
                          }}
                        >
                          <Trash2 className="h-4 w-4 text-red-600" />
                        </Button>
                      </TableCell>
                    </TableRow>

                    {expandedId === r.id && (
                      <TableRow>
                        <TableCell colSpan={7} className="bg-muted/40">
                          <div className="space-y-2">
                            <p className="text-xs font-semibold flex items-center gap-1">
                              <CalendarDays className="h-3 w-3" /> Disponibilità per giorno della settimana
                              <span className="font-normal text-muted-foreground">
                                — risorse (count) e ore per ciascun giorno. 0 = giorno non lavorato.
                              </span>
                            </p>
                            <div className="grid grid-cols-7 gap-2">
                              {WEEKDAYS.map((lbl, wd) => (
                                <div key={wd} className="space-y-1 text-center">
                                  <div className="text-[11px] font-semibold text-muted-foreground">{lbl}</div>
                                  <Input
                                    type="number" min="0" step="1"
                                    aria-label={`${lbl} count`}
                                    value={String(wdDraft[String(wd)]?.count ?? 0)}
                                    onChange={(e) => setWd(wd, "count", e.target.value)}
                                  />
                                  <Input
                                    type="number" min="0" step="0.5"
                                    aria-label={`${lbl} ore`}
                                    value={String(wdDraft[String(wd)]?.hours ?? 0)}
                                    onChange={(e) => setWd(wd, "hours", e.target.value)}
                                  />
                                  <div className="text-[10px] text-muted-foreground">
                                    {(wdDraft[String(wd)]?.count ?? 0) * (wdDraft[String(wd)]?.hours ?? 0)} h
                                  </div>
                                </div>
                              ))}
                            </div>
                            <div className="flex justify-end gap-2">
                              <Button variant="outline" size="sm" onClick={() => setExpandedId(null)}>
                                Annulla
                              </Button>
                              <Button size="sm" disabled={updateMut.isPending} onClick={() => saveWeekly(r.id)}>
                                <Save className="h-4 w-4 mr-1" /> Salva disponibilità
                              </Button>
                            </div>
                          </div>
                        </TableCell>
                      </TableRow>
                    )}
                    </Fragment>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
