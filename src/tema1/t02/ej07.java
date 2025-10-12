package tema1.t02;

import java.util.Scanner;

public class ej07 {
    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);
        System.out.println("Cual es la velocidad en km/h, lo convertiremos a m/s");
        double km = sc.nextDouble();

        double ms = (km * 1000) / 3600;

        System.out.println("Su velocidad en km/h es: " + km + " y ahora lo convertimos a: " + ms + " m/s");
    }
}
