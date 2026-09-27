export type ListChip = {
  id: string;
  label: string;
};

export type ListSortOption = {
  value: string;
  label: string;
};

type ListControlsProps = {
  chips: ListChip[];
  chip: string;
  onChip: (id: string) => void;
  chipGroupLabel: string;
  query: string;
  onQuery: (query: string) => void;
  searchLabel: string;
  searchPlaceholder: string;
  sort: string;
  onSort: (value: string) => void;
  sortOptions: ListSortOption[];
  note?: string;
};

export function ListControls({
  chips,
  chip,
  onChip,
  chipGroupLabel,
  query,
  onQuery,
  searchLabel,
  searchPlaceholder,
  sort,
  onSort,
  sortOptions,
  note
}: ListControlsProps) {
  return (
    <>
      <div className="toolbar" role="group" aria-label={chipGroupLabel}>
        {chips.map((item) => (
          <button
            key={item.id}
            type="button"
            className={chip === item.id ? "chip chip-active" : "chip"}
            aria-pressed={chip === item.id}
            onClick={() => onChip(item.id)}
          >
            {item.label}
          </button>
        ))}
      </div>
      <div className="toolbar">
        <input
          className="field-input run-search"
          type="search"
          aria-label={searchLabel}
          placeholder={searchPlaceholder}
          value={query}
          onChange={(event) => onQuery(event.target.value)}
        />
        <label>
          Sort{" "}
          <select className="field-input" aria-label="Sort" value={sort} onChange={(event) => onSort(event.target.value)}>
            {sortOptions.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
      </div>
      {note ? <p className="run-filters-note">{note}</p> : null}
    </>
  );
}
