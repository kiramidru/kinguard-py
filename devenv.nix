{ ... }:
{
  languages.python = {
    enable = true;
    version = "3.13";
    venv.enable = true;
    uv = {
      enable = true;
      sync.enable = true;
    };
  };
}
