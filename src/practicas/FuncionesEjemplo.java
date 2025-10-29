package practicas;

import java.util.Scanner;

public class FuncionesEjemplo {
    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);

        int precioinicial = 500;
        System.out.println("Cuantos anios tienes");
        int edad = sc.nextInt();
        System.out.println("has tenido accidentes? true/false");
        boolean accidentes = sc.nextBoolean();

        double preciofinal = CalcularSeguro(edad, precioinicial, accidentes);

        System.out.println("Tu seguro costara: " + preciofinal);
    }

    static double CalcularSeguro(int edad, int precioinicial, boolean accidentes) {
        if (edad >= 18 && edad < 25) {
            return (20 * precioinicial) / 100 + precioinicial;
        } else if (edad > 35 && edad < 55 && accidentes == false) {
            return (precioinicial * 0.10) - precioinicial;
        } else if (edad > 65 && accidentes == false) {
            return (10 * precioinicial) / 100 + precioinicial;
        } else if (edad > 65 && accidentes == true) {
            return (30 * precioinicial) / 100 + precioinicial;
        } else {
            return precioinicial;
        }
    }
}


