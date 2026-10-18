export function UploadArtwork({ count }: { count: number }) {
  return (
    <svg viewBox="0 0 260 175" fill="none" aria-hidden="true" className="upload-artwork">
      <g transform="rotate(-17 72 92)">
        <rect x="31" y="48" width="87" height="106" rx="10" fill="var(--primary-deep)" />
        <rect x="45" y="69" width="54" height="4" rx="2" fill="var(--artwork-line)" />
        <rect x="45" y="81" width="40" height="4" rx="2" fill="var(--artwork-line)" />
        <rect x="45" y="93" width="47" height="4" rx="2" fill="var(--artwork-line)" />
      </g>
      <g transform="rotate(16 191 93)">
        <rect x="145" y="43" width="88" height="109" rx="10" fill="var(--primary-deep)" />
        <rect x="159" y="64" width="56" height="4" rx="2" fill="var(--artwork-line)" />
        <rect x="159" y="76" width="40" height="4" rx="2" fill="var(--artwork-line)" />
        <rect x="159" y="88" width="49" height="4" rx="2" fill="var(--artwork-line)" />
      </g>
      <path d="M94 22C94 16.5 98.5 12 104 12H159L184 37V145C184 151 179 156 173 156H104C98.5 156 94 151.5 94 146V22Z" fill="var(--artwork-paper)" />
      <path d="M159 12V30C159 34 162 37 166 37H184L159 12Z" fill="var(--artwork-fold)" />
      <rect x="110" y="48" width="39" height="4" rx="2" fill="var(--artwork-rule)" />
      <rect x="110" y="60" width="56" height="4" rx="2" fill="var(--artwork-rule)" />
      <rect x="110" y="72" width="48" height="4" rx="2" fill="var(--artwork-rule)" />
      <path d="M22 104C22 97 28 91 35 91H95C103 91 108 96 113 101H224C231 101 237 107 237 114V152C237 160 231 166 223 166H36C28 166 22 160 22 152V104Z" fill="var(--primary-deep)" />
      <path d="M22 118C22 111 28 105 35 105H91C102 105 108 114 121 114H224C231 114 237 120 237 127V152C237 160 231 166 223 166H36C28 166 22 160 22 152V118Z" fill="var(--primary)" />
      <path d="M24 132C58 143 83 124 112 136C143 148 178 131 235 139V153C235 160 229 165 222 165H37C30 165 24 159 24 152V132Z" fill="var(--artwork-wave)" fillOpacity=".64" />
      <circle cx="130" cy="128" r="18" fill="var(--artwork-paper)" />
      {count > 0 ? (
        <text x="130" y="134" textAnchor="middle" fontSize="18" fontWeight="700" fill="var(--primary-deep)">
          {count > 99 ? "99+" : count}
        </text>
      ) : (
        <path d="M130 137V120m0 0-6 6m6-6 6 6M121 139h18" stroke="var(--primary-deep)" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
      )}
    </svg>
  );
}
