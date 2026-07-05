import { useSearchParams } from "react-router-dom";

export type TabItem<T extends string> = {
  id: T;
  label: string;
};

type TabBarProps<T extends string> = {
  tabs: TabItem<T>[];
  activeTab: T;
  onChange: (tab: T) => void;
  paramKey?: string;
};

export function TabBar<T extends string>({ tabs, activeTab, onChange, paramKey = "tab" }: TabBarProps<T>) {
  const [, setSearchParams] = useSearchParams();

  const select = (tab: T) => {
    onChange(tab);
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set(paramKey, tab);
        return next;
      },
      { replace: true }
    );
  };

  return (
    <div className="tabs" role="tablist">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          role="tab"
          aria-selected={activeTab === tab.id}
          className={activeTab === tab.id ? "tab-active" : ""}
          onClick={() => select(tab.id)}
          type="button"
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}
