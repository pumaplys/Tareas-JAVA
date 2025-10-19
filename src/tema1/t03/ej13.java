package tema1.t03;

import java.util.Scanner;

public class ej13 {
    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);

        System.out.println("Introduce los segundos del concierto: ");
        int segcon = sc.nextInt();

        int horas = segcon / 3600;
        int resto = segcon % 3600;
        int minutos = resto / 60;
        int segundos = segcon % 60;

        System.out.println("el concierto duro: " + horas + ":" + minutos + ":" + segundos);
    }
}
